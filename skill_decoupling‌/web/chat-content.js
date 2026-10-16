/* Controlled Markdown. Raw HTML is escaped; no remote library or image loading. */
const ChatContent = (() => {
  const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function hideThink(value) {
    const source=String(value??'');let output='',cursor=0,depth=0,match;
    const tags=/<\/?think\b[^>]*>/gi;
    while((match=tags.exec(source))) {
      if(!depth)output+=source.slice(cursor,match.index);
      depth=match[0][1]==='/'?Math.max(0,depth-1):depth+1;cursor=tags.lastIndex;
    }
    if(!depth)output+=source.slice(cursor);
    return output.replace(/<(?:t|th|thi|thin|think)(?:\s[^>]*)?$/i,'').trim();
  }
  function inline(source) {
    const tokens=/(`+)([^\n]*?)\1|\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*([^\n]+?)\*\*|__([^\n]+?)__|~~([^\n]+?)~~|\*([^*\n]+)\*/g;
    let result='',last=0,m;
    while((m=tokens.exec(source))) {
      result+=escape(source.slice(last,m.index));last=tokens.lastIndex;
      if(m[1])result+='<code>'+escape(m[2])+'</code>';
      else if(m[3])result+=/^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(m[4])?'<a href="'+escape(m[4])+'" target="_blank" rel="noopener noreferrer">'+escape(m[3])+'</a>':escape(m[3]);
      else if(m[5]||m[6])result+='<strong>'+escape(m[5]||m[6])+'</strong>';
      else if(m[7])result+='<del>'+escape(m[7])+'</del>';
      else result+='<em>'+escape(m[8])+'</em>';
    }
    return result+escape(source.slice(last));
  }
  function cells(line) {
    line=line.trim().replace(/^\|/,'').replace(/(?<!\\)\|$/,'');
    let cell='',code=false,escaped=false,result=[];
    for(const char of line) {
      if(escaped){cell+=char;escaped=false;continue;}
      if(char==='\\'){escaped=true;continue;}
      if(char==='`')code=!code;
      if(char==='|'&&!code){result.push(cell.trim());cell='';}else cell+=char;
    }
    result.push(cell.trim());return result;
  }
  const divider=line=>line.includes('|')&&cells(line).every(c=>/^:?-+:?$/.test(c));
  function markdown(value,depth=0) {
    if(depth>12)return '<p>'+inline(value)+'</p>';
    const lines=String(value).replace(/\r\n?/g,'\n').split('\n');let html='',i=0;
    const block=line=>/^(?:\s*$|#{1,6}\s|\s*```|\s*~~~|>\s?|\s*(?:[-+*]|\d+[.)])\s|\s*(?:---+|\*\*\*+)\s*$)/.test(line);
    while(i<lines.length) {
      const line=lines[i];if(!line.trim()){i++;continue;}
      const fence=line.match(/^\s*(`{3,}|~{3,})([\w+-]*)\s*$/);
      if(fence) {
        const code=[];i++;while(i<lines.length&&!new RegExp('^\\s*'+fence[1][0]+'{'+fence[1].length+',}\\s*$').test(lines[i]))code.push(lines[i++]);
        i++;html+='<div class="code-block">'+(fence[2]?'<div class="code-language">'+escape(fence[2])+'</div>':'')+'<pre><code>'+escape(code.join('\n'))+'</code></pre></div>';continue;
      }
      if(i+1<lines.length&&line.includes('|')&&divider(lines[i+1])) {
        const headers=cells(line),align=cells(lines[i+1]).map(c=>c.endsWith(':')?(c.startsWith(':')?'center':'right'):'left');i+=2;
        html+='<div class="table-scroll" tabindex="0" role="region" aria-label="回复表格"><table><thead><tr>'+headers.map((c,n)=>'<th style="text-align:'+(align[n]||'left')+'">'+inline(c)+'</th>').join('')+'</tr></thead><tbody>';
        while(i<lines.length&&lines[i].trim()&&lines[i].includes('|')) {
          const row=cells(lines[i++]);html+='<tr>'+headers.map((_,n)=>'<td style="text-align:'+(align[n]||'left')+'">'+inline(row[n]||'')+'</td>').join('')+'</tr>';
        }
        html+='</tbody></table></div>';continue;
      }
      const heading=line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
      if(heading){const level=Math.min(heading[1].length+1,6);html+='<h'+level+'>'+inline(heading[2])+'</h'+level+'>';i++;continue;}
      if(/^\s*(?:---+|\*\*\*+)\s*$/.test(line)){html+='<hr>';i++;continue;}
      if(/^>/.test(line)) {
        const quote=[];while(i<lines.length&&/^>/.test(lines[i]))quote.push(lines[i++].replace(/^>\s?/,''));
        html+='<blockquote>'+markdown(quote.join('\n'),depth+1)+'</blockquote>';continue;
      }
      const list=line.match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/);
      if(list) {
        const ordered=/\d/.test(list[2]),indent=list[1].length,tag=ordered?'ol':'ul';html+='<'+tag+(ordered?' start="'+parseInt(list[2],10)+'"':'')+'>';
        while(i<lines.length) {
          const item=lines[i].match(/^(\s*)([-+*]|\d+[.)])\s+(.*)$/);
          if(!item||item[1].length!==indent||/\d/.test(item[2])!==ordered)break;
          const body=[item[3]];i++;
          while(i<lines.length&&lines[i].trim()&&lines[i].match(/^\s*/)[0].length>indent){const next=lines[i++];body.push(next.slice(Math.min(indent+2,next.match(/^\s*/)[0].length)));}
          html+='<li>'+markdown(body.join('\n'),depth+1)+'</li>';
        }
        html+='</'+tag+'>';continue;
      }
      const paragraph=[line];i++;
      while(i<lines.length&&!block(lines[i])&&!(i+1<lines.length&&divider(lines[i+1])))paragraph.push(lines[i++]);
      html+='<p>'+paragraph.map(inline).join('<br>')+'</p>';
    }
    return html;
  }
  return {hideThink,markdown,render:(element,value)=>{element.innerHTML=markdown(hideThink(value));}};
})();
if(typeof module!=='undefined')module.exports=ChatContent;
