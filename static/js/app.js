const menuButton = document.querySelector("[data-menu-button]");
const menu = document.querySelector("[data-menu]");

if (menuButton && menu) {
  menuButton.addEventListener("click", () => menu.classList.toggle("open"));
}

const galleryMain = document.querySelector("[data-gallery-main]");
document.querySelectorAll("[data-gallery-thumb]").forEach((button) => {
  button.addEventListener("click", () => {
    galleryMain.src = button.dataset.galleryThumb;
    document.querySelectorAll("[data-gallery-thumb]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

