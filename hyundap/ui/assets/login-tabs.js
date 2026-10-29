// B16: 공개 props/watch API만 사용한다. 인증 권한은 서버가 결정한다.
const tabs = Array.from(element.querySelectorAll('[role="tab"]'));
const tablist = element.querySelector('[role="tablist"]');
const sync = () => {
  const admin = props.value === '관리자';
  tablist.dataset.mode = admin ? 'admin' : 'employee';
  tabs.forEach(tab => {
    const selected = tab.dataset.value === props.value;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  element.querySelector('h1').textContent = admin ? '관리자 로그인' : '로그인';
  element.querySelector('.hd-login-heading p').textContent = admin
    ? '문서 검색과 업로드를 관리할 수 있어요.' : '회사 계정으로 로그인해 주세요.';
  const panel = document.getElementById('hd-login-form');
  if (panel) {
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', admin ? 'hd-login-admin-tab' : 'hd-login-employee-tab');
  }
};
const select = (tab, focus = false) => {
  props.value = tab.dataset.value; // Gradio가 change를 자동 발생시킨다.
  sync();
  if (focus) tab.focus();
};
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => select(tab));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    select(tabs[next], true);
  });
});
watch('value', sync);
sync();
