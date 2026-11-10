export async function mount(root) {
  root.innerHTML = `
    <div class="pg-tactical">
      <iframe class="tactical-frame" src="/viz/hq/" title="HQ 战术大屏"></iframe>
    </div>`;
  return () => {};
}
