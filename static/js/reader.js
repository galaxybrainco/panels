(function () {
  function go(direction) {
    const link = document.querySelector('#reader [data-nav="' + direction + '"]');
    if (link) link.click();
  }
  function typing(event) {
    const tag = (event.target.tagName || '').toLowerCase();
    return ['input', 'textarea', 'select'].includes(tag) || event.target.isContentEditable;
  }
  document.addEventListener('keydown', function (event) {
    if (typing(event)) return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); go('prev'); }
    if (event.key === 'ArrowRight') { event.preventDefault(); go('next'); }
  });
  let start = null;
  document.addEventListener('touchstart', function (event) {
    start = { x: event.touches[0].clientX, y: event.touches[0].clientY };
  }, { passive: true });
  document.addEventListener('touchend', function (event) {
    if (start === null) return;
    const dx = event.changedTouches[0].clientX - start.x;
    const dy = event.changedTouches[0].clientY - start.y;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      go(dx < 0 ? 'next' : 'prev');
    }
    start = null;
  }, { passive: true });
  function preload() {
    const reader = document.getElementById('reader');
    if (!reader || !reader.dataset.nextImage) return;
    const image = new Image();
    image.src = reader.dataset.nextImage;
  }
  document.addEventListener('DOMContentLoaded', preload);
  document.addEventListener('htmx:afterSwap', preload);
})();
