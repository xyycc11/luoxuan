/* 纯表现层：只读取已提交结果，不改状态、抽牌顺序或按钮可用性。 */
(() => {
  'use strict';
  const colors={pyro:'#ed764c',hydro:'#559fe0',electro:'#b38ae8',cryo:'#87d8e9',
    dendro:'#9bbb4c',anemo:'#67c9a5',geo:'#dcb753',neutral:'#d3be80'};
  const seen=new Set();
  let effects=[];
  const enabled=()=>document.getElementById('cardFxEnabled')?.checked!==false
    &&!window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function animate(node,frames,options){
    if(!node.animate)return null;
    const animation=node.animate(frames,options);
    animation.finished.catch(()=>{});
    return animation;
  }
  function cleanup(){
    for(const item of effects){item.animation?.cancel();if(item.remove)item.node.remove()}
    effects=[];
  }
  function transient(node,frames,options){
    document.body.append(node);
    const animation=animate(node,frames,options);
    if(!animation){node.remove();return}
    const item={node,animation,remove:true};effects.push(item);
    animation.finished.then(()=>{node.remove();effects=effects.filter(x=>x!==item)},()=>{});
  }
  function deal(hand,key){
    const fresh=hand.dataset.fxHandKey!==key;
    hand.dataset.fxHandKey=key;
    if(!fresh||!enabled())return;
    const cards=[...hand.querySelectorAll('.tactic-card:not(.hold-card)')]
      .filter(card=>card.dataset.element!=='neutral');
    cards.forEach((card,i)=>{
      const animation=animate(card,[{opacity:0,transform:'translate(35px,65px) rotate(7deg) scale(.85)'},
        {opacity:1,transform:'translate(0,0) rotate(0deg) scale(1)'}],
        {duration:360,delay:i*55,easing:'cubic-bezier(.18,.8,.3,1)',fill:'backwards'});
      if(animation)effects.push({node:card,animation,remove:false});
    });
  }
  function capture(action,option){
    if(!/^(element_(pyro|hydro|electro|cryo|dendro|anemo|geo)|card_\w+)$/.test(action||''))return null;
    const card=[...document.querySelectorAll('#cardHand button[data-action]')].find(node=>node.dataset.action===action);
    if(!card)return null;
    const rect=card.getBoundingClientRect();
    return {title:option?.title||card.querySelector('strong')?.textContent||'出牌',
      color:colors[option?.element]||colors.neutral,
      x:rect.left+rect.width/2,y:Math.min(window.innerHeight-60,Math.max(60,rect.top+rect.height/2))};
  }
  function resolve(result,from,previous){
    // 回执恢复、重复点击和手动刷新不重复播放命中动画。
    if(!result.ok||!result.request_id||seen.has(result.request_id))return;
    seen.add(result.request_id);if(seen.size>200)seen.delete(seen.values().next().value);
    if(!enabled())return;
    const sameEnemy=previous?.combat?.round_no===result.state?.combat?.round_no;
    const target=document.getElementById(sameEnemy?'enemyPortrait':'battleCard');
    if(from){
      const box=(target?.getClientRects().length?target:document.getElementById('battleCard')).getBoundingClientRect();
      const x=Math.max(40,Math.min(window.innerWidth-40,box.left+box.width/2));
      const y=Math.max(60,Math.min(window.innerHeight-80,box.top+box.height/2));
      const ghost=document.createElement('div');ghost.className='card-fx-ghost';ghost.setAttribute('aria-hidden','true');
      ghost.textContent=from.title;ghost.style.setProperty('--fx-color',from.color);
      ghost.style.left=from.x+'px';ghost.style.top=from.y+'px';
      transient(ghost,[{opacity:.95,transform:'translate(-50%,-50%) rotate(-6deg) scale(1)'},
        {opacity:1,transform:`translate(calc(-50% + ${x-from.x}px),calc(-50% + ${y-from.y}px)) rotate(8deg) scale(.65)`,offset:.7},
        {opacity:0,transform:`translate(calc(-50% + ${x-from.x}px),calc(-50% + ${y-from.y}px)) scale(.2)`}],
        {duration:480,easing:'cubic-bezier(.25,.65,.3,1)'});
      const hit=document.createElement('div');hit.className='card-fx-hit';hit.setAttribute('aria-hidden','true');
      hit.style.left=x+'px';hit.style.top=y+'px';hit.style.setProperty('--fx-color',from.color);
      transient(hit,[{opacity:0,transform:'translate(-50%,-50%) scale(.2)'},
        {opacity:.8,offset:.25},{opacity:0,transform:'translate(-50%,-50%) scale(2.2)'}],
        {duration:400,delay:240,easing:'ease-out'});
      const animation=target&&animate(target,[{transform:'translateX(0)',filter:'brightness(1)'},
        {transform:'translateX(-7px)',filter:'brightness(1.5)',offset:.3},
        {transform:'translateX(6px)',offset:.55},{transform:'translateX(0)',filter:'brightness(1)'}],
        {duration:280,delay:300});
      if(animation)effects.push({node:target,animation,remove:false});
    }
    const before=previous?.combat?.elemental?.hp,after=result.state?.combat?.elemental?.hp;
    if(Number.isFinite(before)&&Number.isFinite(after)&&after<before){
      const panel=document.getElementById('elementPanel');
      const animation=animate(panel,[{boxShadow:'inset 0 0 0 0 transparent'},
        {boxShadow:'inset 0 0 30px #b8443844',offset:.25},{boxShadow:'inset 0 0 0 0 transparent'}],{duration:450});
      if(animation)effects.push({node:panel,animation,remove:false});
    }
  }
  window.EchoCardFX={deal,capture,resolve,cleanup};
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cleanup()});
})();
