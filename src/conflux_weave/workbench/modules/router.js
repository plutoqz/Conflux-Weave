/* 分区路由：hash 驱动的视图注册表。
   新增分区 = 注册一个视图模块 + 在 index.html 顶栏加一个 data-section-link。 */

const views = new Map();
let active = null;

const SECTION_IDS = {
  overview: "overview-view",
  chat: "chat-view",
  settings: "settings-view",
};

export function registerView(name, handlers) {
  views.set(name, handlers);
}

export function navigate(hash) {
  if (window.location.hash === hash) return;
  window.location.hash = hash;
}

/* 研究视图内部选中 Run 时静默同步地址（不触发 hashchange）。 */
export function replaceHash(hash) {
  if (window.location.hash === hash) return;
  history.replaceState(null, "", hash);
}

function parseHash() {
  const raw = window.location.hash || "#/overview";
  const match = raw.match(/^#\/([a-z]+)(?:\/([^/?#]+))?/);
  if (!match) return { name: "overview", param: null };
  const name = views.has(match[1]) ? match[1] : "overview";
  return { name, param: match[2] ? decodeURIComponent(match[2]) : null };
}

async function apply() {
  const { name, param } = parseHash();
  if (active && active.name === name) {
    if (active.param !== param) {
      active.param = param;
      await views.get(name).onParam?.(param);
    }
    return;
  }
  if (active) await views.get(active.name).unmount?.();
  active = { name, param };

  document.querySelectorAll("[data-section-link]").forEach((link) => {
    if (link.dataset.sectionLink === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const shell = document.querySelector(".app-shell");
  if (shell) shell.dataset.section = name;
  for (const [section, id] of Object.entries(SECTION_IDS)) {
    const node = document.getElementById(id);
    if (node) node.hidden = section !== name;
  }
  if (name !== "research") {
    const emptyView = document.getElementById("empty-view");
    const runView = document.getElementById("run-view");
    if (emptyView) emptyView.hidden = true;
    if (runView) runView.hidden = true;
  }
  window.scrollTo(0, 0);
  await views.get(name)?.mount?.(param);
}

export function initRouter() {
  window.addEventListener("hashchange", apply);
  apply();
}
