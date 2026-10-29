'use strict';
// Busca somente itens ativos, por SKU/nome; nunca aceita um produto digitado pelo usuário.
(() => {
  const input = document.getElementById('sku-input');
  const display = document.getElementById('product-display');
  const results = document.getElementById('sku-results');
  const hint = document.getElementById('sku-hint');
  const form = document.getElementById('damage-form');
  if (!input || !display || !results || !form) return;
  let selected = null;
  let options = [];
  let activeIndex = -1;
  let controller = null;
  let timer = null;
  const close = () => { results.replaceChildren(); results.hidden = true; input.setAttribute('aria-expanded', 'false'); activeIndex = -1; };
  const choose = item => {
    selected = item.sku;
    input.value = item.sku;
    display.value = item.produto;
    hint.textContent = item.preco_disponivel ? 'SKU selecionado. O valor será calculado com base no catálogo.' : 'SKU selecionado. Preço não cadastrado: valor financeiro ficará pendente.';
    close();
  };
  const render = items => {
    options = items;
    results.replaceChildren();
    if (!items.length) { close(); hint.textContent = 'Nenhum SKU ativo encontrado. Solicite cadastro ao administrador.'; return; }
    items.forEach((item,i) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'sku-choice';
      button.setAttribute('role','option'); button.id = `sku-choice-${i}`;
      const sku = document.createElement('strong'); sku.textContent = item.sku;
      const name = document.createElement('span'); name.textContent = item.produto;
      button.append(sku, name);
      button.addEventListener('mousedown',ev => ev.preventDefault());
      button.addEventListener('click',() => choose(item));
      results.appendChild(button);
    });
    results.hidden = false; input.setAttribute('aria-expanded','true');
    hint.textContent = 'Selecione um produto da lista para continuar.';
  };
  const refresh = () => {
    if (controller) controller.abort();
    const query = input.value.trim();
    selected = null; display.value = ''; options = []; close();
    if (!query) { hint.textContent = 'Digite o SKU e escolha uma das sugestões.'; return; }
    controller = new AbortController();
    const myController = controller;
    fetch(`/api/products?q=${encodeURIComponent(query)}`, {credentials:'same-origin',signal:myController.signal,cache:'no-store'})
      .then(response => { if(!response.ok) throw Error('Falha na consulta'); return response.json(); })
      .then(data => { if (controller === myController && input.value.trim()===query) render(data.items || []); })
      .catch(e => { if (e.name !== 'AbortError') hint.textContent = 'Não foi possível consultar os produtos. Confira a conexão.'; });
  };
  input.addEventListener('input',() => { clearTimeout(timer); selected=null;display.value='';close(); timer=setTimeout(refresh,180); });
  input.addEventListener('keydown', event => {
    if (results.hidden || !options.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); activeIndex = (activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      Array.from(results.children).forEach((child,i) => child.classList.toggle('is-active',i===activeIndex));
    } else if (event.key === 'Enter' && activeIndex>=0) {
      event.preventDefault(); choose(options[activeIndex]);
    } else if(event.key === 'Escape') close();
  });
  input.addEventListener('blur',() => { setTimeout(close,160); });
  form.addEventListener('submit',event => {
    if (selected !== input.value.trim().toUpperCase() || !display.value) {
      event.preventDefault(); hint.textContent='Selecione um SKU válido da lista antes de salvar.';
      input.focus();
    }
  });
})();
