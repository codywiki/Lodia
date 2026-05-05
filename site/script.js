const header = document.querySelector("[data-header]");
const form = document.querySelector("[data-contact-form]");
const note = document.querySelector("[data-form-note]");

const updateHeader = () => {
  if (!header) return;
  header.style.boxShadow = window.scrollY > 12 ? "0 1px 0 rgba(0,0,0,.08)" : "none";
};

window.addEventListener("scroll", updateHeader, { passive: true });
updateHeader();

document.querySelectorAll(".section, .consumer-strip").forEach((element) => {
  element.classList.add("reveal");
});

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.16 }
);

document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const name = data.get("name") || "你";
  note.textContent = `${name}，已记录意向。正式接入前请通过 contact@lodia.cn 完成确认。`;
  form.reset();
});
