import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
sys.path.insert(0,str(ROOT/'Docker/mock_player/src'))
sys.path.insert(0,str(ROOT.parent/'platform' if (ROOT.parent/'platform').exists() else next(ROOT.parent.glob('skill_decoupling*'))))
