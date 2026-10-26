'use strict';
// Lightweight mobile UX; all business validations are enforced on the server.
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('photos');
  if (input) {
    input.addEventListener('change', () => {
      if (input.files.length > 3) {
        alert('Selecione no máximo 3 fotos.');
        input.value = '';
      }
    });
  }
  const form = document.getElementById('damage-form');
  if (form) {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type=submit]');
      if (button) { button.disabled = true; button.textContent = 'Salvando ocorrência…'; }
    });
  }
});
