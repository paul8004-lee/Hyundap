// B17: 자격 증명 불일치에만 두 필드의 접근성 오류 상태를 표시한다.
const sync = () => {
  const root = element.closest('#hd-login');
  if (!root) return;
  const invalid = !!element.querySelector('[data-login-invalid="credentials"]');
  root.querySelectorAll('.hd-login-field input, .hd-login-field textarea').forEach(input => {
    const descriptions = new Set((input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    if (invalid) {
      input.setAttribute('aria-invalid', 'true');
      descriptions.add('hd-login-error');
    } else {
      input.removeAttribute('aria-invalid');
      descriptions.delete('hd-login-error');
    }
    if (descriptions.size) input.setAttribute('aria-describedby', [...descriptions].join(' '));
    else input.removeAttribute('aria-describedby');
  });
  const panel = root.querySelector('#hd-login-form');
  const selected = root.querySelector('[role="tab"][aria-selected="true"]');
  if (panel && selected) {
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', selected.id);
  }
  const button = root.querySelector('#hd-password-toggle');
  const password = root.querySelector('#hd-login-password input');
  if (button && password) {
    const shown = password.type === 'text';
    button.setAttribute('aria-label', shown ? '비밀번호 숨기기' : '비밀번호 표시');
    button.setAttribute('aria-pressed', String(shown));
  }
};
watch('value', sync);
sync();
