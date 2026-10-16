"""飞书加密 Webhook 的 AES-256-CBC 解密，仅使用 Python 标准库。

该实现不保证常数时间，只用于请求签名校验之后的入站消息解密。
不提供通用密钥存储或加密服务；测试使用公开 AES/CBC 向量核对算法。"""
import base64
import hashlib
import json

from .common import Problem


def mul(a, b):
    """在 AES 使用的 GF(2^8) 域中相乘，以 0x11B 多项式约简，每次处理 8 位。"""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = ((a << 1) ^ (0x11B if a & 128 else 0)) & 255
        b >>= 1
    return result


def pow_gf(a, n):
    """用平方乘算法计算有限域幂，供 S-box 求逆元素使用。"""
    result = 1
    while n:
        if n & 1:
            result = mul(result, a)
        a, n = mul(a, a), n >> 1
    return result


def rot(a, n):
    """对单字节循环左移 n 位，并将结果限制在 0..255。"""
    return ((a << n) | (a >> (8 - n))) & 255


SBOX = []
for _i in range(256):
    _v = pow_gf(_i, 254) if _i else 0
    SBOX.append(_v ^ rot(_v, 1) ^ rot(_v, 2) ^ rot(_v, 3) ^ rot(_v, 4) ^ 0x63)
INV = [SBOX.index(i) for i in range(256)]


def expand(key):
    """将 32 字节 AES256 密钥展开为 15 个轮密钥，每个轮密钥 16 字节。
    长度错误抛 ValueError，由飞书信封解析统一转换为业务异常。"""
    if len(key) != 32:
        raise ValueError("AES256 requires 32 bytes")
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    rcon = 1
    for i in range(8, 60):
        temp = words[-1].copy()
        if i % 8 == 0:
            temp = [SBOX[x] for x in temp[1:] + temp[:1]]
            temp[0] ^= rcon
            rcon = mul(rcon, 2)
        elif i % 8 == 4:
            temp = [SBOX[x] for x in temp]
        words.append([a ^ b for a, b in zip(words[i - 8], temp)])
    return [sum(words[i:i + 4], []) for i in range(0, 60, 4)]


def decrypt_block(block, keys):
    """解密一个 16 字节块；状态按列排列，依次逆移行、逆代换和逆混列。
    输入 keys 是 expand 的轮密钥；第零轮省略逆混列。"""
    state = [a ^ b for a, b in zip(block, keys[14])]
    for round in range(13, -1, -1):
        state = [INV[state[((col - row) % 4) * 4 + row]] for col in range(4) for row in range(4)]
        state = [a ^ b for a, b in zip(state, keys[round])]
        if round:
            mixed = []
            for col in range(4):
                values = state[col * 4:col * 4 + 4]
                for row in range(4):
                    factors = (14, 11, 13, 9)
                    result = 0
                    for index, value in enumerate(values):
                        result ^= mul(value, factors[(index - row) % 4])
                    mixed.append(result)
            state = mixed
    return bytes(state)


def decrypt_cbc(key, iv, ciphertext):
    """解密整段 CBC 密文，使用前一密文块与当前解密块异或恢复明文。
    本函数只做块运算，不移除填充；IV 必须 16 字节且密文为非空整块。"""
    if len(iv) != 16 or not ciphertext or len(ciphertext) % 16:
        raise ValueError("invalid CBC envelope")
    keys, result, previous = expand(key), bytearray(), iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        result.extend(a ^ b for a, b in zip(decrypt_block(block, keys), previous))
        previous = block
    return bytes(result)


def decrypt_feishu(encrypt_key, envelope):
    """解析 base64(IV + 密文)，用 Encrypt Key 的 SHA256 作为 AES 密钥。
    优先移除 PKCS7 填充，并兼容旧实现的零填充，最后解码 JSON。
    格式或密钥错误统一返回 401，不公开明文或密钥。"""
    try:
        combined = base64.b64decode(envelope, validate=True)
        raw = decrypt_cbc(hashlib.sha256(encrypt_key.encode()).digest(), combined[:16], combined[16:])
        padding = raw[-1]
        if 1 <= padding <= 16 and raw[-padding:] == bytes([padding]) * padding:
            raw = raw[:-padding]
        else:
            raw = raw.rstrip(b"\0")
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError, IndexError):
        raise Problem("CALLBACK_DECRYPT_FAILED", "飞书回调解密失败", 401)
