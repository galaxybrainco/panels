(function () {
  function go(direction) {
    const link = document.querySelector('#reader [data-nav="' + direction + '"]');
    if (link) link.click();
  }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowLeft') go('prev');
    if (event.key === 'ArrowRight') go('next');
  });
  let startX = null;
  document.addEventListener('touchstart', function (event) {
    startX = event.touches[0].clientX;
  }, { passive: true });
  document.addEventListener('touchend', function (event) {
    if (startX === null) return;
    const delta = event.changedTouches[0].clientX - startX;
    if (Math.abs(delta) > 50) go(delta < 0 ? 'next' : 'prev');
    startX = null;
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
