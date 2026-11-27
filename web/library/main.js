const byId = id => document.getElementById(id);
const tenantNames = { north: "北馆", south: "南馆" };
const adminPages = {
  overview: { path: "/admin", title: "工作台", heading: "overview-title" },
  portal: { path: "/admin/portal", group: "社区服务", title: "门户与账号设置", heading: "portal-settings-title" },
  help: { path: "/admin/help", group: "问答与知识库管理", title: "人工问答", heading: "help-title" },
  community: { path: "/admin/community", group: "社区服务", title: "内容发布", heading: "community-title" },
  management: { path: "/admin/books", group: "图书管理", title: "馆藏管理", heading: "management-title" },
  knowledge: { path: "/admin/knowledge", group: "问答与知识库管理", title: "知识库", heading: "knowledge-title" },
  agent: { path: "/admin/agent", group: "问答与知识库管理", title: "Agent 配置", heading: "agent-settings-title" },
  reader: { path: "/admin/agent/preview", group: "问答与知识库管理", title: "Agent 试用", heading: "ask-heading" },
  models: { path: "/admin/settings/models", group: "数据统计与系统管理", title: "模型管理", heading: "models-title" },
  accounts: { path: "/admin/settings/accounts", group: "用户与权限管理", title: "账号管理", heading: "accounts-title" },
};
const adminViews = new Map(Object.entries(adminPages).map(([name, page]) => [page.path, name]));
adminViews.set("/admin/settings", "models");
adminViews.set("/admin/models", "models");
adminViews.set("/admin/accounts", "accounts");

let account = null;
let view = new AbortController();
let questionPoll = new AbortController();

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...options.headers, "X-Library-UI": "1" },
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    const messages = {
      invalid_credentials: "登录已失效，请重新登录。",
      phone_login_mismatch: "该手机号与登录类型不匹配。",
      invalid_password: "账号、登录身份或密码不正确。未设置密码时可使用手机验证码登录。",
      username_unavailable: "该用户名已被使用，请换一个。",
      invalid_current_password: "当前密码不正确。",
      password_login_blocked: "密码尝试次数过多。请稍后再试或使用验证码登录。",
      invalid_new_phone: "请输入与原手机号不同的新号码。",
      invalid_code: "验证码不正确、已失效或已使用，请重新获取。",
      code_cooldown: "验证码已发送，请稍后再试。",
      phone_login_unavailable: "当前环境未启用手机号登录。",
      phone_unavailable: "该手机号已登记或不可用。",
      account_not_found: "未找到本馆账号。",
      account_version_conflict: "账号已被更新，请关闭后重新打开再编辑。",
      cannot_change_own_account: "请由另一名管理员调整当前登录账号。",
      unknown_model_provider: "这个供应商当前不可用。",
      model_catalog_unavailable: "供应商的模型列表暂不可用，可以选择已有预设或稍后重试。",
      model_credentials_changed: "供应商凭据刚刚变更，请刷新后重试。",
      model_not_in_catalog: "模型目录已变化，请刷新后重试。",
      model_key_required: "请填写该供应商的 API Key。",
      model_name_conflict: "该供应商下已有同名连接。",
      invalid_answer_model: "请选择本馆已通过连接测试的模型。",
      model_timeout: "模型响应超时，请检查供应商状态后重试。",
      model_auth_failed: "供应商拒绝了认证，请核对 API Key。",
      model_rate_limited: "供应商当前限流，请稍后重试。",
      staff_required: "此操作需要馆员身份。",
      file_too_large: "电子书超过上传大小上限。",
      unsupported_file: "不支持该文件格式。",
      notice_feed_not_approved: "该外部公告源未在本馆可选列表内。",
      notice_feed_configuration_invalid: "外部公告源部署配置无效。",
      portal_settings_conflict: "设置已被其他馆员更新，请重新进入页面。",
      otp_login_disabled: "本馆已关闭读者手机验证码登录，请使用账号密码或重置密码。",
      sms_delivery_failed: "验证码发送失败，请稍后重试。",
      sms_delivery_limited: "当前发送请求较多，请稍后重试。",
      registration_disabled: "本馆暂未开放自助注册。",
      activity_full: "活动名额已满。",
      enrollment_closed: "活动报名已关闭。",
      guide_visibility_fixed: "题包资料的可见范围固定，可编辑内容或撤回。",
      activity_has_enrollments: "已有读者报名，请保留活动类型和报名名额。",
      help_already_answered: "这条咨询已由其他馆员回复。",
      community_post_not_found: "未找到本馆发布内容。",
      community_post_conflict: "内容已被更新，请刷新列表后重试。",
      community_post_state_invalid: "当前状态无法执行该操作，请刷新列表。",
      invalid_request: "请检查必填项和内容长度。",
      knowledge_version_unavailable: "该版本已不再生效，请刷新文档列表。",
      book_archived: "图书已删除。恢复后可继续编辑、上传或发布。",
      book_revision_conflict: "图书资料已被更新，请重新打开后再操作。",
      version_not_ready: "该版本仍在处理，请等解析完成后发布。",
      upload_not_retryable: "版本状态已变化，或需要更换文件后再上传。",
    };
    throw new Error(messages[problem.detail] ?? "操作未成功，请检查输入后重试。");
  }
  return response.json();
}

const jobLabels = { queued: "排队中", running: "处理中", succeeded: "已完成", failed: "失败" };
const sources = { books: "电子书", library: "馆规与服务" };
let jobs = [];
let historyRequest = 0;
let activeQuestion = null;
const questionMode = () => byId("question-form").elements["question-mode"].value;
const isPreview = () => account?.role === "staff";

function setQuestionMode(mode) {
  byId("question-form").elements["question-mode"].value = mode;
  const books = mode === "books";
  for (const button of document.querySelectorAll(".consultation-categories button")) {
    button.setAttribute("aria-pressed", String(button.dataset.consultMode === mode));
  }
  const branch = tenantNames[account.tenant_id] ?? account.tenant_id;
  byId("question").placeholder = books ? "输入书名，或询问书里的内容" : "例如，储物柜怎么用？";
  byId("question-scope").textContent = branch + (books ? " · 检索已发布且对你可见的电子书。"
    : " · 查询馆内服务。也可输入英文关键词，例如 locker。");
}

const readerHeadings = {
  "reader-home": "portal-title", "ask-library": "ask-heading", "reader-catalog": "reader-catalog-title",
  "borrow-guide": "borrow-guide-title", "community-services": "community-services-title", "reader-faq": "reader-faq-title",
  "my-questions": "history-title", "personal-center": "reader-profile-title",
};

function renderReaderSection() {
  const hash = location.hash.slice(1);
  const defaultSection = new URLSearchParams(location.search).has("question") ? "ask-library" : "reader-home";
  const current = isPreview() ? "ask-library" : Object.hasOwn(readerHeadings, hash) ? hash : defaultSection;
  byId("reader-view").dataset.section = current;
  for (const section of Object.keys(readerHeadings)) {
    if (section !== "my-questions") byId(section).hidden = section !== current;
  }
  byId("consultation-faq").hidden = current !== "ask-library";
  byId("preview-knowledge").hidden = !isPreview();
  byId("browse-from-faq").hidden = isPreview();
  for (const link of byId("reader-nav").querySelectorAll("a")) {
    if (link.hash === "#" + current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const selectedLink = byId("reader-nav").querySelector('[aria-current="page"]');
  byId("reader-breadcrumb-page").textContent = current === "reader-home" ? "服务大厅" : selectedLink.textContent;
  if (["reader-home", "reader-faq", "ask-library"].includes(current) && !isPreview()) void loadReaderPortal();
  if (current === "personal-center") void loadProfile();
  if (current === "borrow-guide") void loadReaderGuides();
  if (current === "reader-home" || current === "community-services") void loadReaderNotices();
  return current;
}

function setReaderMenu(open) {
  byId("reader-menu").setAttribute("aria-expanded", String(open));
  byId("reader-nav").classList.toggle("is-open", open);
}

byId("reader-menu").addEventListener("click", () => {
  setReaderMenu(byId("reader-menu").getAttribute("aria-expanded") !== "true");
});

function navigateReader(section) {
  const hash = "#" + section;
  if (!isPreview() && location.hash !== hash) history.pushState(null, "", hash);
  setReaderMenu(false);
  renderReaderSection();
}

function openConversation() {
  navigateReader("ask-library");
}

document.addEventListener("click", event => {
  const link = event.target.closest("#reader-nav a, [data-portal-link]");
  if (!link || account?.role !== "reader" || event.button !== 0 ||
      event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const section = link.hash.slice(1);
  navigateReader(section);
  byId(readerHeadings[section]).focus({ preventScroll: true });
  window.scrollTo(0, 0);
});

byId("portal-question-form").addEventListener("submit", event => {
  event.preventDefault();
  openConversation();
  byId("question").value = byId("portal-question").value.trim();
  if (byId("question").value) byId("question-form").requestSubmit();
  else byId("question").focus();
});
for (const button of document.querySelectorAll("[data-consult-mode]")) {
  button.addEventListener("click", () => {
    openConversation();
    setQuestionMode(button.dataset.consultMode);
    byId("question").focus();
  });
}
for (const button of document.querySelectorAll("[data-ask-example]")) {
  button.addEventListener("click", () => {
    openConversation();
    byId(button.dataset.askExample).click();
  });
}

window.addEventListener("hashchange", () => {
  if (account && !byId("reader-view").hidden) {
    setReaderMenu(false);
    renderReaderSection();
  }
});

byId("new-question").addEventListener("click", () => {
  if (byId("ask").disabled) return;
  questionPoll.abort();
  questionPoll = new AbortController();
  activeQuestion = pendingSubmission = null;
  byId("previous-turns").replaceChildren();
  byId("conversation-status").textContent = "";
  openConversation();
  setQuestionLocation();
  renderHistory();
  byId("question-result").hidden = true;
  byId("question-welcome").hidden = false;
  setQuestionMode("books");
  byId("question").value = "";
  byId("question").focus();
});

function renderHistory() {
  const panel = byId("job-history");
  const filter = byId("history-filter").value;
  const filtered = jobs.filter(job => !filter || (filter === "pending" ? ["queued", "running"].includes(job.status) : filter === job.status));
  if (!filtered.length) {
    panel.textContent = jobs.length ? "没有符合条件的问答。" : "你还没有问答记录。";
    return;
  }
  const list = document.createElement("ul");
  for (const job of filtered) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const title = document.createElement("span");
    const status = document.createElement("small");
    title.textContent = job.query;
    const created = new Date(job.created_at);
    status.textContent = [sources[job.source_mode], jobLabels[job.status] ?? job.status,
      created.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })].join(" · ");
    status.title = created.toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" });
    button.type = "button";
    if (job.conversation_id === activeQuestion?.conversation_id) button.setAttribute("aria-current", "true");
    button.append(title, status);
    button.addEventListener("click", () => {
      if (!byId("ask").disabled) void loadConversation(job.conversation_id, true);
    });
    item.append(button);
    list.append(item);
  }
  panel.replaceChildren(list);
}

async function showHistory() {
  const signal = view.signal;
  const request = ++historyRequest;
  try {
    const search = new URLSearchParams({ search: byId("history-search").value.trim() });
    const items = await api("/v1/conversations?" + search, { signal });
    if (signal.aborted || request !== historyRequest) return;
    jobs = items;
    renderHistory();
  } catch (error) {
    if (!signal.aborted && request === historyRequest) byId("job-history").textContent = error.message;
  }
}
byId("refresh-history").addEventListener("click", () => void showHistory());
byId("history-search").addEventListener("input", () => void showHistory());
byId("history-filter").addEventListener("change", renderHistory);

let publishedBooks = [];

function renderReaderCatalog() {
  const list = byId("reader-catalog-items");
  const search = byId("reader-catalog-search").value.trim().toLowerCase();
  const category = bookCategories("reader-category", publishedBooks);
  const matches = publishedBooks.filter(book => (!category || book.category === category) &&
    [book.title, book.author, book.isbn].some(value => value?.toLowerCase().includes(search)));
  list.replaceChildren();
  if (!matches.length) {
    list.textContent = publishedBooks.length ? "没有找到匹配图书。" : "本馆暂时没有对你可见的已发布电子书。";
    return;
  }
  for (const book of matches) {
    const details = document.createElement("details");
    const title = document.createElement("summary");
    const meta = document.createElement("p");
    const ask = document.createElement("button");
    title.textContent = book.title;
    meta.textContent = bookMetadata(book);
    meta.hidden = !meta.textContent;
    ask.type = "button";
    ask.className = "secondary";
    ask.textContent = "用书名提问";
    ask.addEventListener("click", () => {
      openConversation();
      setQuestionMode("books");
      byId("question").value = "《" + book.title + "》";
      byId("question").focus();
    });
    details.append(title, meta, ask);
    list.append(details);
  }
}

async function loadReaderCatalog() {
  const signal = view.signal;
  try {
    const items = await api("/v1/books", { signal });
    if (signal.aborted) return;
    publishedBooks = items.filter(book => book.active_version);
    renderReaderCatalog();
  } catch (error) {
    if (!signal.aborted) byId("reader-catalog-items").textContent = error.message;
  }
}

async function loadReaderGuides() {
  const signal = view.signal;
  const panel = byId("reader-guide-items");
  panel.textContent = "正在读取本馆公开指南…";
  try {
    const guides = await api("/v1/reader/guides", { signal });
    if (signal.aborted) return;
    panel.replaceChildren();
    if (!guides.length) panel.textContent = "本馆暂无公开指南。";
    for (const guide of guides) {
      const item = document.createElement("article");
      const text = document.createElement("p");
      const source = document.createElement("small");
      text.textContent = guide.text;
      source.textContent = "资料编号 " + guide.id;
      if (guide.title) {
        const title = document.createElement("h3");
        title.textContent = guide.title;
        item.append(title);
      }
      item.append(text, source);
      panel.append(item);
    }
  } catch (error) {
    if (!signal.aborted) panel.textContent = error.message;
  }
}

async function loadReaderPortal() {
  const { signal } = view;
  try {
    const portal = await api("/v1/reader/portal", { signal });
    if (signal.aborted) return;
    byId("reader-portal-error").textContent = "";
    byId("portal-title").textContent = portal.title;
    byId("portal-intro").textContent = portal.intro;
    for (const link of byId("reader-home").querySelectorAll(".portal-service-grid a")) {
      link.hidden = !portal.shortcuts.includes(link.hash.slice(1));
    }
    byId("portal-services-title").parentElement.hidden = !portal.shortcuts.length;
    byId("reader-faq-items").hidden = byId("portal-default-faq").hidden =
      byId("default-question-suggestions").hidden = portal.custom_faq;
    const list = byId("reader-custom-faq");
    const highlights = byId("portal-custom-faq");
    const questions = byId("custom-question-suggestions");
    for (const panel of [list, highlights, questions]) {
      panel.hidden = !portal.custom_faq;
      panel.replaceChildren();
    }
    for (const faq of portal.faqs) {
      const card = document.createElement("details");
      const title = document.createElement("summary");
      const answer = document.createElement("p");
      title.textContent = faq.title;
      answer.textContent = faq.body;
      card.append(title, answer);
      list.append(card);
      if (faq.featured) {
        const link = document.createElement("a");
        link.href = "#reader-faq";
        link.className = "portal-faq";
        link.dataset.portalLink = "";
        link.textContent = faq.title;
        highlights.append(link);
        const ask = document.createElement("button");
        ask.type = "button";
        ask.textContent = faq.title;
        ask.addEventListener("click", () => {
          setQuestionMode("library");
          byId("question").value = faq.title;
          byId("question-form").requestSubmit();
        });
        questions.append(ask);
      }
    }
    if (portal.custom_faq && !highlights.childElementCount) highlights.textContent = "本馆暂无推荐问题。";
    filterReaderFaq();
  } catch (error) {
    if (!signal.aborted) byId("reader-portal-error").textContent = error.message;
  }
}

const communityKinds = { notice: "通知公告", activity: "社区活动", guide: "借阅指南", faq: "常见问题", handbook: "馆员手册" };
const communityStates = { draft: "草稿", published: "已发布", withdrawn: "已撤回" };
const communityDate = new Intl.DateTimeFormat("zh-CN", {dateStyle: "medium", timeStyle: "short"});

function readerFeedEntry(notice) {
  const entry = document.createElement("article");
  const link = document.createElement("a");
  link.href = notice.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = notice.title || "查看馆方公告";
  entry.append(link);
  if (notice.published !== "未标明") {
    const date = document.createElement("small");
    date.textContent = notice.published;
    entry.append(date);
  }
  return entry;
}

function readerCommunityEntry(post, summary) {
  const entry = document.createElement("article");
  const title = document.createElement(summary ? "a" : "h3");
  if (summary) {
    title.href = "#community-services";
    title.dataset.portalLink = "";
  }
  title.textContent = post.title;
  const meta = document.createElement("small");
  meta.textContent = communityKinds[post.kind] + " · " + communityDate.format(new Date(post.published_at));
  entry.className = "community-entry";
  entry.append(title, meta);
  if (!summary) {
    const content = document.createElement("p");
    content.textContent = post.body;
    entry.append(content);
    if (post.kind === "activity") renderEnrollment(entry, post);
  }
  return entry;
}

function renderEnrollment(entry, post) {
  const details = document.createElement("p");
  const fields = [];
  if (post.starts_at) fields.push(communityDate.format(new Date(post.starts_at)));
  if (post.location) fields.push(post.location);
  if (post.capacity) fields.push("已报名 " + post.enrolled_count + " / " + post.capacity);
  details.textContent = fields.join(" · ");
  if (fields.length) entry.append(details);
  const upcoming = post.starts_at && Date.parse(post.starts_at) > Date.now();
  if (!post.capacity || (!upcoming && !post.enrolled)) return;
  const action = document.createElement("button");
  const feedback = document.createElement("small");
  action.className = "secondary";
  action.type = "button";
  const full = !post.enrolled && post.enrolled_count >= post.capacity;
  action.textContent = post.enrolled ? "取消报名" : full ? "名额已满" : "报名参加";
  action.disabled = full;
  feedback.setAttribute("role", "status");
  action.addEventListener("click", async () => {
    const { signal } = view;
    action.disabled = true;
    try {
      await api("/v1/reader/activities/" + post.id + "/enrollment",
        { method: post.enrolled ? "DELETE" : "PUT", signal });
      if (!signal.aborted) void loadReaderNotices();
    } catch (error) {
      if (!signal.aborted) {
        feedback.textContent = error.message;
        action.disabled = false;
      }
    }
  });
  entry.append(action, feedback);
}

let noticeRequest = null;
async function loadReaderNotices() {
  if (noticeRequest) return;
  const { signal } = view;
  const panels = [byId("portal-notices"), byId("reader-notices")];
  panels.forEach(panel => { panel.textContent = "正在读取本馆通知…"; });
  const request = Promise.allSettled([
    api("/v1/reader/community-posts", { signal }),
    api("/v1/reader/notices", { signal }),
  ]);
  noticeRequest = request;
  try {
    const [community, feed] = await request;
    if (signal.aborted) return;
    const posts = community.value ?? [];
    const external = feed.value?.items ?? [];
    for (const panel of panels) {
      const summary = panel.id === "portal-notices";
      const entries = posts.map(post => readerCommunityEntry(post, summary));
      entries.push(...external.map(readerFeedEntry));
      panel.replaceChildren(...(summary ? entries.slice(0, 3) : entries));
      if (community.status === "rejected" || feed.status === "rejected" || feed.value?.state === "unavailable") {
        const error = document.createElement("p");
        error.textContent = entries.length ? "部分通知暂时无法读取。" : "暂时无法读取本馆通知。";
        panel.append(error);
      } else if (!entries.length) {
        panel.textContent = "本馆暂无已发布的公告或活动。";
      }
    }
  } finally {
    if (noticeRequest === request) noticeRequest = null;
  }
}

byId("refresh-notices").addEventListener("click", () => void loadReaderNotices());
function filterReaderFaq() {
  const search = byId("reader-faq-search").value.trim().toLowerCase();
  const source = byId("reader-faq-items").hidden ? "reader-custom-faq" : "reader-faq-items";
  const items = [...byId(source).querySelectorAll("details")];
  for (const item of items) item.hidden = !item.textContent.toLowerCase().includes(search);
  byId("reader-faq-empty").hidden = items.some(item => !item.hidden);
  byId("reader-faq-empty").textContent = items.length ? "没有匹配的问题。" : "本馆暂无已发布的常见问题。";
}
byId("reader-faq-search").addEventListener("input", filterReaderFaq);

byId("reader-category").addEventListener("change", renderReaderCatalog);
byId("reader-catalog-search").addEventListener("input", renderReaderCatalog);
byId("refresh-catalog").addEventListener("click", () => void loadReaderCatalog());

function profileAction(path, method, fields) {
  return api("/v1/profile" + path, {
    method,
    signal: view.signal,
    headers: { "Content-Type": "application/json" },
    body: fields ? JSON.stringify(fields) : undefined,
  });
}

function renderProfile(profile) {
  byId("profile-phone-masked").textContent = profile.phone_masked;
  byId("profile-password-state").textContent = profile.has_password ? "已设置" : "未设置";
  byId("profile-password-action").textContent = profile.has_password ? "修改" : "设置";
  byId("profile-current-password-fields").hidden = !profile.has_password;
  byId("profile-current-password").disabled = !profile.has_password;
}

async function loadProfile() {
  const settings = byId("profile-settings");
  const status = byId("profile-status");
  if (!settings.hidden || status.dataset.loading) return;
  status.dataset.loading = "true";
  status.textContent = "正在读取个人资料…";
  const { signal } = view;
  try {
    const profile = await profileAction("", "GET");
    byId("profile-name").value = profile.display_name;
    renderProfile(profile);
    status.textContent = "";
    settings.hidden = false;
  } catch (error) {
    if (!signal.aborted) status.textContent = error.message + " 可重新进入个人中心重试。";
  } finally {
    if (!signal.aborted) delete status.dataset.loading;
  }
}

const profileForms = {
  name: { path: "", method: "PATCH", success: "昵称已保存。" },
  phone: { path: "/phone", method: "POST", success: "手机号已更换，其他浏览器登录会话已退出。" },
  password: { path: "/password", method: "POST", success: "密码已保存，其他浏览器登录会话已退出。" },
};

byId("profile-new-phone").addEventListener("input", () => {
  byId("message-phone-new").textContent = "";
  byId("profile-new-code").value = "";
});

for (const button of document.querySelectorAll("[data-profile-code]")) {
  button.addEventListener("click", async () => {
    const purpose = button.dataset.profileCode;
    const newPhone = byId("profile-new-phone");
    const status = byId("message-" + purpose);
    const phone = newPhone.value;
    if (purpose === "phone-new" && !newPhone.reportValidity()) return;
    const fields = purpose === "phone-new" ? { purpose, new_phone: phone } : { purpose };
    const { signal } = view;
    button.disabled = true;
    status.textContent = "";
    try {
      const result = await profileAction("/code", "POST", fields);
      if (purpose !== "phone-new" || newPhone.value === phone) {
        showAuthCode("message-" + purpose, result);
      }
    } catch (error) {
      if (!signal.aborted) status.textContent = error.message;
    } finally {
      if (!signal.aborted) button.disabled = false;
    }
  });
}

let savingProfile = false;
for (const [section, config] of Object.entries(profileForms)) {
  const form = byId("profile-" + section + "-form");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (savingProfile || byId("sign-out").disabled) return;
    const status = byId("profile-" + section + "-status");
    if (section === "password" && byId("profile-new-password").value !== byId("profile-password-confirm").value) {
      status.textContent = "两次输入的密码不一致。";
      byId("profile-password-confirm").focus();
      return;
    }
    const { signal } = view;
    const fields = Object.fromEntries(new FormData(form));
    const controls = [...form.elements].filter(element => !element.disabled);
    savingProfile = true;
    status.textContent = "";
    for (const control of controls) control.disabled = true;
    let saved = null;
    try {
      saved = await profileAction(config.path, config.method, fields);
      if (section === "name") {
        byId("profile-name").value = saved.display_name;
      } else {
        form.reset();
        for (const message of document.querySelectorAll('#profile-settings [id^="message-"]')) {
          message.textContent = "";
        }
        for (const input of document.querySelectorAll('#profile-settings input[inputmode="numeric"]:not([type="tel"])')) {
          input.value = "";
        }
      }
      status.textContent = config.success;
    } catch (error) {
      if (!signal.aborted) status.textContent = error.message;
    } finally {
      for (const control of controls) control.disabled = false;
      if (saved) renderProfile(saved);
      savingProfile = false;
    }
  });
}

function resetProfile() {
  byId("profile-settings").hidden = true;
  delete byId("profile-status").dataset.loading;
  byId("profile-status").textContent = "";
  for (const form of byId("profile-settings").querySelectorAll("form")) form.reset();
  for (const element of byId("profile-settings").querySelectorAll('[role="status"]')) element.textContent = "";
  for (const element of byId("profile-settings").querySelectorAll("details")) element.open = false;
  for (const button of document.querySelectorAll("[data-profile-code]")) button.disabled = false;
}

function showWorkspace(user) {
  account = user;
  const library = tenantNames[user.tenant_id] ?? user.tenant_id;
  const admin = user.role === "staff";
  document.body.classList.toggle("admin", admin);
  document.body.classList.toggle("reader", !admin);
  byId("account-name").textContent = library + " · " + (admin ? "管理员" : "读者");
  byId("reader-profile-identity").textContent = admin ? "" : byId("account-name").textContent;
  byId("workspace-title").textContent = admin ? "馆藏管理后台" : library + "图书馆";
  loginForm.reset();
  updateLoginMethod();
  resetRegistration();
  loginForm.hidden = false;
  registerForm.hidden = resetForm.hidden = true;
  byId("auth-reset-success").textContent = "";
  byId("login").hidden = true;
  byId("workspace").hidden = byId("account").hidden = false;
  byId("staff-nav").hidden = byId("admin-toolbar").hidden = !admin;
  setAdminMenu(false);
  byId("toggle-preview-history").setAttribute("aria-expanded", "false");
  byId("reader-nav").hidden = admin;
  byId("ask-heading").textContent = admin ? "Agent 试用" : "智能问答";
  setQuestionMode("books");
  restoreWorkspaceView();
}

byId("toggle-preview-history").addEventListener("click", event => {
  const button = event.currentTarget;
  button.setAttribute("aria-expanded", String(button.getAttribute("aria-expanded") !== "true"));
});

const loginForm = byId("login-form");
const registerForm = byId("register-form");
const loginType = () => loginForm.elements.login_type.value;
const passwordMode = () => loginForm.elements.login_method.value === "password";

function authError(id, message = "") {
  const error = byId(id);
  error.textContent = message;
  error.hidden = !message;
}

function updateLoginMethod() {
  const password = passwordMode();
  byId("password-reset-link").hidden = !password;
  byId("login-code-fields").hidden = byId("phone").disabled = byId("code").disabled = password;
  byId("login-password-fields").hidden = byId("login-identifier").disabled = byId("login-password").disabled = !password;
  byId("login-password").value = "";
  byId("code").value = "";
  byId("code-message").textContent = "";
  authError("login-error");
  byId("demo-hint").textContent = loginType() === "admin"
    ? "本地测试手机号 13800000002 或 13800000004"
    : "本地测试手机号 13800000001 或 13800000003";
}

for (const radio of [...loginForm.elements.login_method, ...loginForm.elements.login_type]) {
  radio.addEventListener("change", updateLoginMethod);
}
byId("phone").addEventListener("input", () => {
  byId("code-message").textContent = "";
  byId("code").value = "";
});
updateLoginMethod();

function authAction(action, fields) {
  return api("/v1/auth/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
}

async function performAuth(form, errorId, action) {
  const fields = [...form.elements].filter(field => !field.disabled);
  for (const field of fields) field.disabled = true;
  authError(errorId);
  try {
    await action();
  } catch (error) {
    authError(errorId, error.message);
  } finally {
    for (const field of fields) field.disabled = false;
    if (form === loginForm && byId("login").hidden) updateLoginMethod();
  }
}

function showAuthCode(messageId, response) {
  byId(messageId).textContent = response.local_code
    ? "本地验证码 " + response.local_code + "，5 分钟内有效。"
    : "验证码已发送。";
}

byId("send-code").addEventListener("click", async () => {
  if (!byId("phone").reportValidity()) return;
  const fields = { phone: byId("phone").value, login_type: loginType() };
  await performAuth(loginForm, "login-error", async () => {
    showAuthCode("code-message", await authAction("code", fields));
  });
  byId("code").focus();
});

loginForm.addEventListener("submit", event => {
  event.preventDefault();
  if (byId("login-submit").disabled) return;
  const password = passwordMode();
  const fields = { login_type: loginType() };
  if (password) {
    const identifier = byId("login-identifier").value.trim();
    if (/^1[3-9][0-9]{9}$/.test(identifier)) fields.phone = identifier;
    else if (/^[a-zA-Z][a-zA-Z0-9_-]{3,23}$/.test(identifier)) fields.username = identifier;
    else {
      authError("login-error", "请输入用户名或已绑定的 11 位手机号。");
      byId("login-identifier").focus();
      return;
    }
    fields.password = byId("login-password").value;
  } else {
    fields.phone = byId("phone").value;
    fields.code = byId("code").value;
  }
  void performAuth(loginForm, "login-error", async () => {
    showWorkspace(await authAction(password ? "password-login" : "login", fields));
  });
});

function clearRegistrationCode() {
  byId("register-code").value = "";
  byId("register-code-message").textContent = "";
}

function resetRegistration() {
  registerForm.reset();
  clearRegistrationCode();
  authError("register-error");
}

byId("open-registration").addEventListener("click", () => {
  loginForm.reset();
  updateLoginMethod();
  loginForm.hidden = true;
  registerForm.hidden = false;
  byId("register-title").focus();
});
byId("back-to-login").addEventListener("click", () => {
  resetRegistration();
  registerForm.hidden = true;
  loginForm.hidden = false;
  byId("login-title").focus();
});
byId("register-phone").addEventListener("input", clearRegistrationCode);
byId("register-tenant").addEventListener("change", clearRegistrationCode);

byId("register-send-code").addEventListener("click", async () => {
  const tenant = byId("register-tenant");
  const phone = byId("register-phone");
  if (!tenant.reportValidity() || !phone.reportValidity()) return;
  const fields = { tenant_id: tenant.value, phone: phone.value };
  await performAuth(registerForm, "register-error", async () => {
    showAuthCode("register-code-message", await authAction("register/code", fields));
  });
  byId("register-code").focus();
});

registerForm.addEventListener("submit", event => {
  event.preventDefault();
  if (byId("register-submit").disabled) return;
  const fields = Object.fromEntries(new FormData(registerForm));
  if (fields.password !== byId("register-confirm").value) {
    authError("register-error", "两次输入的密码不一致。");
    byId("register-confirm").focus();
    return;
  }
  void performAuth(registerForm, "register-error", async () => {
    showWorkspace(await authAction("register", fields));
  });
});

const resetForm = byId("password-reset-form");
function clearResetCode() {
  byId("reset-code").value = "";
  byId("reset-code-message").textContent = "";
}

function showPasswordReset(open) {
  resetForm.reset();
  clearResetCode();
  authError("reset-error");
  byId("auth-reset-success").textContent = "";
  if (open) byId("reset-role").value = loginType();
  loginForm.hidden = open;
  resetForm.hidden = !open;
  byId(open ? "password-reset-title" : "login-title").focus();
}
byId("open-password-reset").addEventListener("click", () => showPasswordReset(true));
byId("cancel-password-reset").addEventListener("click", () => showPasswordReset(false));
byId("reset-phone").addEventListener("input", clearResetCode);
byId("reset-role").addEventListener("change", clearResetCode);
byId("reset-send-code").addEventListener("click", async () => {
  if (!byId("reset-phone").reportValidity()) return;
  const fields = { phone: byId("reset-phone").value, login_type: byId("reset-role").value };
  await performAuth(resetForm, "reset-error", async () => {
    showAuthCode("reset-code-message", await authAction("password-reset/code", fields));
  });
  byId("reset-code").focus();
});
resetForm.addEventListener("submit", event => {
  event.preventDefault();
  if (byId("reset-submit").disabled) return;
  const fields = Object.fromEntries(new FormData(resetForm));
  if (fields.password !== byId("reset-confirm").value) {
    authError("reset-error", "两次输入的密码不一致。");
    return;
  }
  void performAuth(resetForm, "reset-error", async () => {
    await authAction("password-reset", fields);
    showPasswordReset(false);
    loginForm.querySelector('input[name="login_type"][value="' + fields.login_type + '"]').checked = true;
    updateLoginMethod();
    byId("login-identifier").value = fields.phone;
    byId("auth-reset-success").textContent = "密码已重置，请用新密码登录。";
    byId("login-password").focus();
  });
});

for (const id of ["reader-profile-signout", "reader-nav-signout"]) {
  byId(id).addEventListener("click", () => {
    byId("account-menu").open = true;
    byId("sign-out").click();
  });
}

byId("sign-out").addEventListener("click", async () => {
  if (savingProfile) {
    byId("account-error").textContent = "资料正在保存，请稍候再退出。";
    return;
  }
  byId("sign-out").disabled = true;
  try {
    await api("/v1/auth/logout", { method: "POST" });
  } catch (error) {
    byId("account-error").textContent = error.message;
    return;
  } finally {
    byId("sign-out").disabled = false;
  }
  byId("account-error").textContent = "";
  account = null;
  view.abort();
  view = new AbortController();
  resetProfile();
  document.body.classList.remove("admin", "reader");
  questionPoll.abort();
  activeQuestion = pendingSubmission = null;
  byId("previous-turns").replaceChildren();
  byId("conversation-status").textContent = "";
  jobs = [];
  overviewRequest++;
  byId("refresh-overview").disabled = false;
  byId("review-rows").replaceChildren();
  byId("review-table").hidden = true;
  byId("review-empty").textContent = "";
  for (const id of ["metric-books", "metric-review", "metric-questions", "metric-accounts",
    "questions-pending", "questions-completed", "questions-failed"]) byId(id).textContent = "—";
  byId("account-search").value = byId("account-filter-role").value = byId("account-filter-state").value = "";
  byId("history-search").value = byId("history-filter").value = "";
  byId("reader-nav").hidden = true;
  byId("account-menu").open = false;
  history.replaceState(null, "", "/");
  resetManagement();
  byId("account-dialog").close();
  setProviderCredentialPanel(false);
  showModelPane(false);
  byId("model-rows").replaceChildren();
  byId("model-providers").replaceChildren();
  byId("account-rows").replaceChildren();
  byId("account-form").reset();
  byId("workspace").hidden = byId("account").hidden = true;
  byId("job-history").replaceChildren();
  byId("reader-catalog-items").replaceChildren();
  byId("portal-notices").replaceChildren();
  byId("reader-notices").replaceChildren();
  byId("reader-guide-items").replaceChildren();
  noticeRequest = null;
  resetCommunity();
  setReaderMenu(false);
  byId("reader-faq-search").value = "";
  byId("reader-faq-search").dispatchEvent(new Event("input"));
  byId("reader-catalog-search").value = byId("portal-question").value = byId("reader-category").value = "";
  byId("portal-title").textContent = "在这里，遇见更多知识";
  byId("portal-intro").textContent = "查询本馆馆藏、馆内服务和电子书内容。";
  byId("reader-portal-error").textContent = "";
  for (const id of ["portal-custom-faq", "reader-custom-faq", "custom-question-suggestions"]) {
    byId(id).hidden = true;
    byId(id).replaceChildren();
  }
  for (const id of ["portal-default-faq", "reader-faq-items", "default-question-suggestions", "portal-services-title"]) {
    byId(id).hidden = false;
  }
  byId("portal-services-title").parentElement.hidden = false;
  for (const link of byId("reader-home").querySelectorAll(".portal-service-grid a")) link.hidden = false;
  byId("help-inbox").replaceChildren();
  byId("preview-knowledge-status").textContent = "";
  resetKnowledge();
  byId("portal-admin-form").reset();
  helpInboxRequest++;
  portalVersion = null;
  publishedBooks = [];
  accountItems = [];
  byId("question-result").hidden = true;
  byId("citations").replaceChildren();
  byId("answer").textContent = byId("job-status").textContent = "";
  byId("response").hidden = true;
  byId("ask").disabled = false;
  byId("question").value = "";
  byId("login").hidden = false;
  byId("login-identifier").focus();
});
byId("question-form").addEventListener("change", event => {
  if (event.target.name === "question-mode") setQuestionMode(event.target.value);
});
byId("catalog-example").addEventListener("click", () => {
  setQuestionMode("books");
  byId("question").value = "本馆有哪些书";
  byId("question-form").requestSubmit();
});
byId("service-example").addEventListener("click", () => {
  setQuestionMode("library");
  byId("question").value = "储物柜怎么用？ locker";
  byId("question-form").requestSubmit();
});

async function renderAnswer(job, path, signal) {
  byId("answer").textContent = job.result.answer;
  byId("response").hidden = false;
  if (!job.result.citations.length) {
    byId("job-status").textContent = "没有可引用的资料。可以尝试换个关键词或切换资料范围。";
    return;
  }
  await renderCitations(byId("citations"), job.result.citations, path, signal);
}

async function renderCitations(citations, ids, path, signal) {
  citations.textContent = "正在读取资料出处…";
  const items = await Promise.all(ids.map(async id => {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const evidence = document.createElement("p");
    summary.textContent = id;
    details.append(summary, evidence);
    try {
      const source = await api(path + "/citations/" + encodeURIComponent(id), { signal });
      summary.textContent = [source.title ?? source.id, source.locator].filter(Boolean).join(" · ");
      evidence.textContent = source.text;
      if (source.url && /^https?:\/\//i.test(source.url)) {
        const link = document.createElement("a");
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "查看原网页";
        details.append(link);
      }
    } catch (error) {
      evidence.textContent = error.message;
    }
    return details;
  }));
  if (!signal.aborted) citations.replaceChildren(...items);
}

function beginQuestion(query, mode, focus = true) {
  questionPoll.abort();
  questionPoll = new AbortController();
  byId("question-result").hidden = false;
  byId("question-welcome").hidden = true;
  byId("result-question").textContent = query;
  byId("job-source").textContent = sources[mode];
  byId("response").hidden = true;
  byId("citations").replaceChildren();
  byId("reader-help").hidden = true;
  byId("refresh-question").hidden = byId("retry-question").hidden = true;
  byId("job-status").textContent = "正在读取…";
  if (focus) {
    byId("result-title").focus({ preventScroll: true });
    byId("question-result").scrollIntoView({ block: "nearest" });
  }
  return questionPoll.signal;
}

async function loadReaderHelp(jobId, signal, submit = false, panel = byId("reader-help")) {
  const path = "/v1/reader/help/" + encodeURIComponent(jobId);
  const element = id => panel.querySelector("#" + id + ", [data-help-part=" + id + "]");
  const ask = element("request-reader-help");
  const refresh = element("refresh-reader-help");
  const status = element("reader-help-status");
  const reply = element("reader-help-reply");
  panel.hidden = false;
  reply.textContent = "";
  ask.hidden = refresh.hidden = true;
  status.textContent = submit ? "正在发送人工咨询…" : "正在读取人工咨询状态…";
  try {
    if (submit) await api(path, { method: "PUT", signal });
    const item = await api(path, { signal });
    if (signal.aborted) return;
    status.textContent = item?.answered_at ? "馆员已回复" : item ? "已提交，等待本馆馆员回复。" : "";
    reply.textContent = item?.reply ?? "";
    ask.hidden = Boolean(item);
    refresh.hidden = !item || Boolean(item.answered_at);
  } catch (error) {
    if (signal.aborted) return;
    status.textContent = error.message;
    refresh.hidden = false;
  }
}
byId("request-reader-help").addEventListener("click", () => {
  if (activeQuestion?.job_id && window.confirm("将这条问题发送给本馆馆员？")) {
    void loadReaderHelp(activeQuestion.job_id, questionPoll.signal, true);
  }
});
byId("refresh-reader-help").addEventListener("click", () => {
  if (activeQuestion?.job_id) void loadReaderHelp(activeQuestion.job_id, questionPoll.signal);
});

async function trackJob(record, signal, previousPending) {
  const path = "/v1/jobs/" + encodeURIComponent(record.job_id);
  byId("refresh-question").hidden = false;
  try {
    for (let attempt = 0; attempt < 45 && !signal.aborted; attempt++) {
      const job = await api(path, { signal });
      if (signal.aborted) return;
      if (["succeeded", "failed"].includes(job.status)) {
        byId("refresh-question").hidden = true;
        void showHistory();
        if (previousPending) {
          const turns = await api("/v1/conversations/" + encodeURIComponent(record.conversation_id), { signal });
          if (signal.aborted) return;
          const index = turns.findIndex(turn => turn.job_id === record.job_id);
          if (index >= 0) renderPreviousTurns(turns.slice(0, index), signal);
        }
        if (!isPreview()) void loadReaderHelp(record.job_id, signal);
        if (job.status === "succeeded") {
          byId("job-status").textContent = "";
          await renderAnswer(job, path, signal);
        } else {
          byId("job-status").textContent = "处理失败。可以重新提交问题。";
          byId("retry-question").hidden = false;
        }
        return;
      }
      byId("job-status").textContent = job.status === "queued" ? "问题已接收，正在排队。可稍后从我的问答查看。"
        : "正在处理。离开页面后仍可从我的问答查看。";
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (!signal.aborted) byId("job-status").textContent = "任务仍在处理，可以稍后从我的问答打开。";
  } catch (error) {
    if (!signal.aborted) byId("job-status").textContent = error.message + " 可以查看最新状态。";
  }
}

function setQuestionLocation(jobId) {
  const url = new URL(location.href);
  if (jobId) url.searchParams.set("question", jobId);
  else url.searchParams.delete("question");
  history.replaceState(null, "", url);
}

function renderPreviousTurns(turns, signal) {
  const container = byId("previous-turns");
  const items = turns.map(turn => {
    const section = document.createElement("section");
    section.className = "result-panel";
    const question = document.createElement("div");
    question.className = "question-message";
    const source = document.createElement("p");
    source.className = "field-note";
    source.textContent = sources[turn.source_mode];
    const query = document.createElement("p");
    query.className = "previous-question";
    query.textContent = turn.query;
    question.append(source, query);
    const answer = document.createElement("div");
    answer.className = "answer-message";
    const speaker = document.createElement("p");
    speaker.className = "message-label";
    speaker.textContent = "图书馆助手";
    const response = document.createElement("p");
    response.className = "previous-answer";
    response.textContent = turn.result?.answer ?? (jobLabels[turn.status] ?? turn.status);
    answer.append(speaker, response);
    if (turn.result?.citations.length) {
      const references = document.createElement("details");
      const summary = document.createElement("summary");
      const citations = document.createElement("div");
      citations.className = "turn-citations";
      summary.textContent = "查看引用";
      references.append(summary, citations);
      references.addEventListener("toggle", () => {
        if (!references.open || citations.hasChildNodes() || signal.aborted) return;
        void renderCitations(citations, turn.result.citations, "/v1/jobs/" + encodeURIComponent(turn.job_id), signal);
      });
      answer.append(references);
    }
    if (!isPreview() && ["succeeded", "failed"].includes(turn.status)) {
      const help = document.createElement("details");
      const summary = document.createElement("summary");
      const panel = byId("reader-help").cloneNode(true);
      panel.removeAttribute("id");
      const parts = {};
      for (const part of panel.querySelectorAll("[id]")) {
        parts[part.id] = part;
        part.dataset.helpPart = part.id;
        part.removeAttribute("id");
      }
      summary.textContent = "人工咨询";
      help.append(summary, panel);
      panel.hidden = true;
      const load = (submit = false) => void loadReaderHelp(turn.job_id, signal, submit, panel);
      help.addEventListener("toggle", () => { if (help.open && panel.hidden) load(); });
      parts["request-reader-help"].addEventListener("click", () => {
        if (window.confirm("将这条问题发送给本馆馆员？")) load(true);
      });
      parts["refresh-reader-help"].addEventListener("click", () => load());
      answer.append(help);
    }
    section.append(question, answer);
    return section;
  });
  container.replaceChildren(...items);
}

function viewJob(record, focus, previousPending) {
  activeQuestion = record;
  if (focus) openConversation();
  setQuestionLocation(record.conversation_id);
  setQuestionMode(record.source_mode);
  renderHistory();
  const signal = beginQuestion(record.query, record.source_mode, focus);
  void trackJob(record, signal, previousPending);
  return signal;
}

async function loadConversation(jobId, focus = false) {
  questionPoll.abort();
  questionPoll = new AbortController();
  const loading = questionPoll.signal;
  byId("ask").disabled = true;
  const status = byId("conversation-status");
  status.textContent = "正在读取会话…";
  try {
    const turns = await api("/v1/conversations/" + encodeURIComponent(jobId), { signal: loading });
    if (loading.aborted) return;
    const latest = turns.at(-1);
    const previous = turns.slice(0, -1);
    const signal = viewJob(latest, focus, previous.some(turn => ["queued", "running"].includes(turn.status)));
    renderPreviousTurns(previous, signal);
    const transcript = byId("question-result").parentElement;
    transcript.scrollTop = transcript.scrollHeight;
    status.textContent = "";
    byId("ask").disabled = false;
  } catch (error) {
    if (!loading.aborted) status.textContent = error.message + "。请从历史会话重新打开。";
  } finally {
    if (!loading.aborted) byId("ask").disabled = false;
  }
}

async function restoreQuestion() {
  const jobId = new URL(location.href).searchParams.get("question");
  if (jobId) await loadConversation(jobId);
}

byId("refresh-question").addEventListener("click", () => {
  if (activeQuestion) void loadConversation(activeQuestion.conversation_id);
});
byId("retry-question").addEventListener("click", () => {
  if (!activeQuestion) return;
  setQuestionMode(activeQuestion.source_mode);
  byId("question").value = activeQuestion.query;
  byId("question-form").requestSubmit();
});

let pendingSubmission = null;
byId("question-form").addEventListener("submit", async event => {
  event.preventDefault();
  const query = byId("question").value.trim();
  const mode = questionMode();
  if (!account || !query || byId("ask").disabled) return;
  const conversationId = activeQuestion?.conversation_id ?? null;
  if (pendingSubmission?.query !== query || pendingSubmission?.mode !== mode ||
      pendingSubmission?.conversationId !== conversationId) {
    pendingSubmission = { query, mode, conversationId, key: crypto.randomUUID() };
  }
  const status = byId("conversation-status");
  byId("ask").disabled = true;
  status.textContent = "正在发送…";
  const session = view.signal;
  try {
    const { job_id } = await api(mode === "books" ? "/v1/agent/jobs" : "/v1/jobs", {
      signal: session,
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": pendingSubmission.key },
      body: JSON.stringify({ query, conversation_id: conversationId }),
    });
    if (session.aborted) return;
    pendingSubmission = null;
    if (byId("question").value.trim() === query) byId("question").value = "";
    activeQuestion = { job_id, query, source_mode: mode, conversation_id: conversationId ?? job_id };
    setQuestionLocation(activeQuestion.conversation_id);
    void showHistory();
    await loadConversation(activeQuestion.conversation_id, true);
  } catch (error) {
    if (!session.aborted) status.textContent = error.message + "。可重新发送。";
  } finally {
    if (!session.aborted) byId("ask").disabled = false;
  }
});

let versionPoll;
let selectedSnapshot;
let reviewTarget;

function resetManagement() {
  clearTimeout(versionPoll);
  selectedSnapshot = undefined;
  byId("managed-book").value = "";
  byId("book-list").hidden = false;
  byId("book-detail").hidden = true;
  bookDialog.close();
  bookArchiveDialog.close();
  selectedManagementBook = editingBook = archiveTarget = null;
  byId("book-action-notice").textContent = "";
  byId("confirm-book-archive").disabled = byId("cancel-book-archive").disabled = false;
  managementBooks = [];
  byId("book-rows").replaceChildren();
  byId("book-search").value = byId("book-filter").value = byId("book-audience-filter").value = byId("admin-category").value = "";
  byId("book-list-status").textContent = "";
  byId("versions").replaceChildren();
  byId("book-form").reset();
  byId("upload-form").reset();
  byId("upload-status").textContent = byId("book-status").textContent = "";
  byId("close-book-dialog").disabled = byId("book-submit").disabled = byId("upload-submit").disabled = false;
}

let overviewRequest = 0;

async function loadOverview() {
  const signal = view.signal;
  const request = ++overviewRequest;
  const status = byId("overview-status");
  const refresh = byId("refresh-overview");
  const branch = tenantNames[account.tenant_id] ?? account.tenant_id;
  byId("overview-branch").textContent = branch + " · 馆藏、问答与账号";
  status.textContent = "正在读取工作台…";
  refresh.disabled = true;
  try {
    const data = await api("/v1/admin/overview", { signal });
    if (signal.aborted || request !== overviewRequest) return;
    byId("metric-books").textContent = data.published_books;
    byId("metric-review").textContent = data.pending_versions;
    byId("metric-questions").textContent = data.questions.total;
    byId("metric-accounts").textContent = data.active_accounts;
    for (const name of ["pending", "completed", "failed"]) {
      byId("questions-" + name).textContent = data.questions[name];
    }
    const review = byId("review-rows");
    review.replaceChildren();
    for (const item of data.review_queue) {
      const row = review.insertRow();
      const book = row.insertCell();
      const title = document.createElement("strong");
      const file = document.createElement("small");
      title.textContent = item.title;
      file.textContent = item.filename;
      book.append(title, file);
      row.insertCell().textContent = new Date(item.created_at).toLocaleString("zh-CN", {
        dateStyle: "short", timeStyle: "short",
      });
      const link = document.createElement("a");
      link.href = "/admin/books?" + new URLSearchParams({ book: item.book_id, version: item.version_id });
      link.dataset.adminView = "management";
      link.textContent = "查看并审核";
      row.insertCell().append(link);
    }
    byId("review-table").hidden = !data.review_queue.length;
    byId("review-empty").textContent = data.pending_versions > data.review_queue.length
      ? "显示最近上传的 " + data.review_queue.length + " 个版本，其余版本可在馆藏管理查看。"
      : data.review_queue.length ? "" : "当前没有待审核的电子版。";
    status.textContent = "";
  } catch (error) {
    if (!signal.aborted && request === overviewRequest) status.textContent = error.message;
  } finally {
    if (!signal.aborted && request === overviewRequest) refresh.disabled = false;
  }
}

byId("refresh-overview").addEventListener("click", () => void loadOverview());

function setAdminMenu(open) {
  byId("staff-nav").classList.toggle("is-open", open);
  byId("toggle-staff-nav").setAttribute("aria-expanded", String(open));
}

byId("toggle-staff-nav").addEventListener("click", () => {
  setAdminMenu(byId("toggle-staff-nav").getAttribute("aria-expanded") !== "true");
});

function openAdminShortcut(params, buttonId) {
  if (params.get("action") !== "new") return;
  const url = new URL(location.href);
  url.searchParams.delete("action");
  history.replaceState(null, "", url);
  byId(buttonId).click();
}

function restoreWorkspaceView() {
  if (!account) return;
  switchView(account.role === "staff" ? (adminViews.get(location.pathname) ?? "overview") : "reader");
}

function switchView(active, navigate = false, query = "") {
  const admin = account?.role === "staff";
  const page = adminPages[active];
  const route = admin ? page.path : "/reader";
  if (navigate && (location.pathname !== route || location.search !== query)) {
    history.pushState(null, "", route + query);
  } else if (!navigate && location.pathname !== route) {
    history.replaceState(null, "", route + (admin ? location.search : ""));
  }
  for (const name of Object.keys(adminPages)) {
    byId(name + "-view").hidden = active !== name;
  }
  if (admin) {
    for (const link of byId("staff-nav").querySelectorAll("[data-admin-view]")) {
      if (link.dataset.adminView === active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    const crumb = byId("admin-breadcrumb-group");
    crumb.textContent = page.group ?? "";
    crumb.hidden = !page.group;
    byId("admin-breadcrumb-page").textContent = page.title;
    setAdminMenu(false);
  }
  clearTimeout(versionPoll);
  questionPoll.abort();
  byId("ask").disabled = false;
  if (active === "reader") {
    renderReaderSection();
    activeQuestion = null;
    byId("previous-turns").replaceChildren();
    byId("conversation-status").textContent = "";
    byId("question-result").hidden = true;
    byId("question-welcome").hidden = false;
    void showHistory();
    if (admin) void loadKnowledge(true);
    else void loadReaderCatalog();
    void restoreQuestion();
  }
  const params = new URLSearchParams(location.search);
  if (active === "overview") void loadOverview();
  if (active === "community") void loadCommunity();
  if (active === "portal") void loadPortalSettings();
  if (active === "help") void loadHelpInbox();
  if (active === "management") {
    byId("book-filter").value = params.get("status") === "published" ? "published" : "";
    byId("managed-book").value = "";
    selectedManagementBook = null;
    byId("book-action-notice").textContent = "";
    byId("book-detail").hidden = true;
    byId("book-list").hidden = false;
    reviewTarget = params.get("version");
    void loadManagement(params.get("book"));
  }
  if (active === "knowledge") void loadKnowledge();
  if (active === "agent") void loadAgentSettings();
  if (active === "accounts") {
    byId("account-filter-state").value = params.get("status") === "active" ? "true" : "";
    void loadAccounts();
  }
  if (active === "models") void loadModels();
  byId(admin ? page.heading : "workspace-title").focus({ preventScroll: true });
  if (navigate) window.scrollTo(0, 0);
  if (active === "management") openAdminShortcut(params, "add-book");
  if (active === "community") openAdminShortcut(params, "add-community-post");
  if (active === "accounts") openAdminShortcut(params, "add-account");
}

byId("workspace").addEventListener("click", event => {
  const link = event.target.closest("a[data-admin-view]");
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (account?.role === "staff") switchView(link.dataset.adminView, true, link.search);
});
window.addEventListener("popstate", () => {
  if (account?.role === "reader" && location.pathname === "/reader" && !byId("reader-view").hidden) {
    renderReaderSection();
    const savedJob = new URL(location.href).searchParams.get("question");
    if (savedJob !== (activeQuestion?.conversation_id ?? null)) restoreWorkspaceView();
  } else {
    restoreWorkspaceView();
  }
});

async function loadAgentSettings() {
  const signal = view.signal;
  const form = byId("agent-form");
  byId("agent-save").disabled = true;
  byId("agent-status").textContent = "正在读取设置…";
  try {
    const [settings, model, connections] = await Promise.all([
      api("/v1/agent/settings", { signal }),
      api("/v1/agent/model-workbench", { signal }),
      api("/v1/admin/models", { signal }),
    ]);
    if (signal.aborted) return;
    form.elements.search_titles.checked = settings.search_titles;
    form.elements.search_content.checked = settings.search_content;
    form.elements.max_sources.value = settings.max_sources;
    form.elements.prompt.value = settings.prompt;
    byId("agent-default-prompt").textContent = model.default_prompt;
    const choices = form.elements.answer_model;
    choices.replaceChildren(
      new Option(model.mode === "configured" ? "系统默认（工作台绑定模型）" : "系统默认（模拟模型）", "default"),
      new Option("模拟模型", "mock"),
      ...connections.filter(item => item.verified_at && item.kind === "chat").map(item => new Option(modelDisplay(item), item.id)),
    );
    choices.value = settings.answer_model;
    const label = choices.selectedOptions[0]?.textContent ?? "所选模型不可用";
    byId("agent-model-mode").textContent = "当前已保存的回答模型：" + label;
    byId("agent-status").textContent = "";
    byId("agent-save").disabled = false;
  } catch (error) {
    if (!signal.aborted) byId("agent-status").textContent = error.message;
  }
}

byId("agent-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const body = {
    search_titles: form.elements.search_titles.checked,
    search_content: form.elements.search_content.checked,
    max_sources: Number(form.elements.max_sources.value),
    prompt: form.elements.prompt.value,
    answer_model: form.elements.answer_model.value,
  };
  if (!(body.search_titles || body.search_content)) {
    byId("agent-status").textContent = "请至少开启一种检索范围。";
    return;
  }
  const signal = view.signal;
  byId("agent-save").disabled = true;
  try {
    await api("/v1/agent/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    });
    if (!signal.aborted) {
      byId("agent-model-mode").textContent = "当前已保存的回答模型：" + form.elements.answer_model.selectedOptions[0].textContent;
      byId("agent-status").textContent = "已保存，之后处理的问答将使用此设置。";
    }
  } catch (error) {
    if (!signal.aborted) byId("agent-status").textContent = error.message;
  } finally {
    if (!signal.aborted) byId("agent-save").disabled = false;
  }
});

const bookDialog = byId("book-dialog");
const bookArchiveDialog = byId("book-archive-dialog");
let selectedManagementBook = null;
let editingBook = null;
let archiveTarget = null;
const bookStates = { archived: "已删除", empty: "未上传", processing: "处理中", ready: "待发布", published: "已发布", failed: "解析失败" };
let managementBooks = [];
let managementRequest = 0;

function bookState(book) {
  if (book.archived_at) return "archived";
  if (["queued", "running"].includes(book.latest_status)) return "processing";
  if (book.latest_status === "failed") return "failed";
  if (book.latest_id && book.latest_id === book.active_version) return "published";
  return book.latest_status === "ready" ? "ready" : "empty";
}

let knowledgeRequest = 0;
async function loadKnowledge(preview = false) {
  const request = ++knowledgeRequest;
  const { signal } = view;
  const status = byId(preview ? "preview-knowledge-status" : "knowledge-status");
  const refresh = byId("refresh-knowledge");
  status.textContent = "正在读取本馆索引…";
  if (!preview) {
    refresh.disabled = true;
    byId("knowledge-scope").textContent = (tenantNames[account.tenant_id] ?? account.tenant_id) + " · 电子书版本与问答检索";
    knowledgeBooks = null;
    byId("knowledge-index-note").textContent = "";
    byId("knowledge-books-empty").hidden = byId("knowledge-materials-empty").hidden = true;
    byId("knowledge-book-rows").replaceChildren();
    byId("knowledge-material-rows").replaceChildren();
    for (const id of ["knowledge-book-count", "knowledge-passage-count", "knowledge-material-count"]) byId(id).textContent = "—";
  }
  try {
    const data = await api("/v1/agent/knowledge", { signal });
    if (signal.aborted || request !== knowledgeRequest) return;
    const published = data.books.filter(book => book.active_version);
    const passages = published.reduce((total, book) => total + book.passages, 0);
    const materials = data.materials.filter(item => item.state === "published").length;
    if (preview) {
      status.textContent = published.length + " 本已发布电子书 · " + passages + " 段正文 · " + materials + " 条馆规与服务资料";
    } else {
      byId("knowledge-book-count").textContent = published.length;
      byId("knowledge-passage-count").textContent = passages;
      byId("knowledge-material-count").textContent = materials;
      byId("knowledge-index-note").textContent = "已启用" +
        [data.search_titles && "书目", data.search_content && "正文"].filter(Boolean).join("和") +
        "检索。待发布版本审核后才可作为问答资料。";
      renderKnowledge(data);
      status.textContent = "";
    }
  } catch (error) {
    if (!signal.aborted && request === knowledgeRequest) status.textContent = error.message;
  } finally {
    if (!preview && !signal.aborted && request === knowledgeRequest) refresh.disabled = false;
  }
}
byId("refresh-knowledge").addEventListener("click", () => void loadKnowledge());

let knowledgeBooks = null;
let knowledgePreviewRequest = 0;
let knowledgePreviewPage = null;
const knowledgePreview = byId("knowledge-preview");

function resetKnowledge() {
  knowledgeRequest++;
  knowledgePreviewRequest++;
  knowledgePreviewPage = knowledgeBooks = null;
  knowledgePreview.close();
  for (const id of ["knowledge-book-rows", "knowledge-material-rows", "knowledge-search-results", "knowledge-preview-items"]) {
    byId(id).replaceChildren();
  }
  byId("knowledge-search-form").reset();
  byId("knowledge-search-status").textContent = "";
  byId("knowledge-book-filter").value = byId("knowledge-version-filter").value = "";
  setKnowledgeSection("documents");
}

function setKnowledgeSection(name) {
  for (const button of byId("knowledge-view").querySelectorAll("[data-knowledge-section]")) {
    const current = button.dataset.knowledgeSection === name;
    button.setAttribute("aria-pressed", String(current));
    byId(button.getAttribute("aria-controls")).hidden = !current;
  }
}
for (const button of byId("knowledge-view").querySelectorAll("[data-knowledge-section]")) {
  button.addEventListener("click", () => setKnowledgeSection(button.dataset.knowledgeSection));
}

function knowledgeVersionStatus(version) {
  if (version.active) return "当前生效";
  if (version.status === "ready") return version.was_published ? "历史版本" : "待发布";
  return version.status === "failed" ? "解析失败" : "处理中";
}

function knowledgeActionLink(book, version) {
  const link = document.createElement("a");
  const query = new URLSearchParams({ book: book.id });
  if (version) query.set("version", version);
  link.href = "/admin/books?" + query;
  link.dataset.adminView = "management";
  link.textContent = "上传或审核";
  link.className = "knowledge-link";
  return link;
}

function knowledgeLabel(text, state) {
  const label = document.createElement("span");
  label.className = "status-pill";
  label.dataset.state = state;
  label.textContent = text;
  return label;
}

function renderKnowledgeBooks() {
  if (!knowledgeBooks) return;
  const keyword = byId("knowledge-book-filter").value.trim().toLowerCase();
  const state = byId("knowledge-version-filter").value;
  const matches = knowledgeBooks.filter(book => (!state || bookState(book) === state) &&
    [book.title, book.author, book.isbn, book.category, book.shelf].some(value => value?.toLowerCase().includes(keyword)));
  const list = byId("knowledge-book-rows");
  list.replaceChildren();
  const empty = byId("knowledge-books-empty");
  empty.hidden = matches.length > 0;
  empty.textContent = knowledgeBooks.length ? "没有符合筛选条件的文档。" : "本馆暂无馆藏登记。";
  for (const book of matches) {
    const entry = document.createElement("details");
    const header = document.createElement("summary");
    const name = document.createElement("strong");
    const metadata = document.createElement("small");
    const panel = document.createElement("div");
    entry.className = "knowledge-document";
    panel.className = "knowledge-document-versions";
    name.textContent = book.title;
    metadata.textContent = [book.author, book.category, book.shelf, book.audience === "staff" ? "仅本馆馆员" : "本馆读者和馆员"].filter(Boolean).join(" · ");
    const status = bookState(book);
    const stateLabel = book.active_version ? "有生效版本" : bookStates[status];
    header.append(name, knowledgeLabel(stateLabel, book.active_version ? "published" : status), metadata);
    entry.append(header, panel);
    list.append(entry);
    entry.addEventListener("toggle", async () => {
      if (!entry.open || panel.dataset.loaded) return;
      const { signal } = view;
      panel.dataset.loaded = "true";
      panel.textContent = "正在读取版本…";
      try {
        const versions = await api("/v1/books/" + encodeURIComponent(book.id) + "/uploads", { signal });
        if (signal.aborted || !panel.isConnected) return;
        panel.replaceChildren();
        if (!versions.length) panel.textContent = "还没有上传电子书文件。";
        versions.forEach((version, index) => {
          const item = document.createElement("article");
          const description = document.createElement("div");
          const heading = document.createElement("strong");
          const file = document.createElement("small");
          item.className = "knowledge-version";
          heading.textContent = "版本 " + (versions.length - index);
          file.textContent = version.filename;
          if (version.active && book.active_version === version.job_id) file.textContent += " · " + book.passages + " 段正文";
          description.append(heading, knowledgeLabel(knowledgeVersionStatus(version), version.active ? "published" : version.status), file);
          item.append(description);
          if (version.status === "ready") {
            item.append(tableAction(version.active ? "查看正文索引" : "预览正文", () => showBookPassages(book, version)));
          }
          panel.append(item);
        });
        panel.append(knowledgeActionLink(book, versions[0]?.job_id));
      } catch (error) {
        if (!signal.aborted && panel.isConnected) {
          panel.textContent = error.message;
          delete panel.dataset.loaded;
        }
      }
    });
  }
}
byId("knowledge-book-filter").addEventListener("input", renderKnowledgeBooks);
byId("knowledge-version-filter").addEventListener("change", renderKnowledgeBooks);

function renderKnowledge({ books, materials }) {
  knowledgeBooks = books;
  renderKnowledgeBooks();
  const first = byId("knowledge-book-rows").firstElementChild;
  if (first) first.open = true;
  byId("knowledge-materials-empty").hidden = materials.length > 0;
  const rows = byId("knowledge-material-rows");
  rows.replaceChildren();
  for (const item of materials) {
    const row = rows.insertRow();
    const name = row.insertCell();
    name.textContent = item.title;
    if (item.source_id) {
      const source = document.createElement("small");
      source.className = "field-note";
      source.textContent = item.source_id;
      name.append(document.createElement("br"), source);
    }
    row.insertCell().textContent = communityKinds[item.kind];
    row.insertCell().textContent = item.kind === "handbook" ? "仅本馆馆员" : "本馆读者和馆员";
    row.insertCell().textContent = communityStates[item.state];
    row.insertCell().append(tableAction("查看资料", () => showKnowledgeMaterial(item)));
  }
}

function knowledgeExcerpt(parent, heading, content) {
  const card = document.createElement("article");
  const title = document.createElement("h4");
  const text = document.createElement("p");
  title.textContent = heading;
  text.textContent = content;
  card.append(title, text);
  parent.append(card);
}

function openKnowledgePreview(title) {
  byId("knowledge-preview-title").textContent = title;
  byId("knowledge-preview-status").textContent = "正在读取正文…";
  byId("knowledge-preview-items").replaceChildren();
  byId("knowledge-preview-more").hidden = true;
  knowledgePreviewPage = null;
  knowledgePreview.showModal();
  return ++knowledgePreviewRequest;
}

function knowledgePreviewIsCurrent(request, signal) {
  return !signal.aborted && knowledgePreview.open && request === knowledgePreviewRequest;
}

async function showBookPassages(book, version) {
  const request = openKnowledgePreview(book.title);
  const { signal } = view;
  const ids = "/books/" + encodeURIComponent(book.id) + "/versions/" + encodeURIComponent(version.job_id);
  if (version.active) {
    await appendBookPassages("/v1/agent/knowledge" + ids + "/passages", 0, request, signal);
    return;
  }
  try {
    const preview = await api("/v1" + ids + "/preview", { signal });
    if (!knowledgePreviewIsCurrent(request, signal)) return;
    byId("knowledge-preview-status").textContent = "此版本当前未生效，最多预览前 8 段。";
    const items = byId("knowledge-preview-items");
    for (const part of preview) knowledgeExcerpt(items, part.locator, part.text);
  } catch (error) {
    if (knowledgePreviewIsCurrent(request, signal)) byId("knowledge-preview-status").textContent = error.message;
  }
}

async function appendBookPassages(path, offset, request, signal) {
  const more = byId("knowledge-preview-more");
  more.disabled = true;
  try {
    const data = await api(path + "?offset=" + offset, { signal });
    if (!knowledgePreviewIsCurrent(request, signal)) return;
    const items = byId("knowledge-preview-items");
    if (offset === 0) {
      const metadata = [["作者", data.author], ["分类", data.category], ["ISBN", data.isbn], ["馆藏位置", data.shelf],
        ["可见范围", data.audience === "staff" ? "仅本馆馆员" : "本馆读者和馆员"]]
        .filter(([, value]) => value).map(([label, value]) => label + " " + value);
      knowledgeExcerpt(items, "书目信息", metadata.join("\n"));
    }
    for (const part of data.items) knowledgeExcerpt(items, "片段 " + (part.ordinal + 1) + " · " + part.locator, part.text);
    byId("knowledge-preview-status").textContent = items.childElementCount > 1 ? "当前生效版本" : "当前版本没有正文片段。";
    knowledgePreviewPage = data.next_offset === null ? null : { path, offset: data.next_offset, request, signal };
    more.hidden = !knowledgePreviewPage;
  } catch (error) {
    if (knowledgePreviewIsCurrent(request, signal)) byId("knowledge-preview-status").textContent = error.message;
  } finally {
    if (knowledgePreviewIsCurrent(request, signal)) more.disabled = false;
  }
}
byId("knowledge-preview-more").addEventListener("click", () => {
  if (!knowledgePreviewPage) return;
  const { path, offset, request, signal } = knowledgePreviewPage;
  void appendBookPassages(path, offset, request, signal);
});

async function showKnowledgeMaterial(item) {
  const request = openKnowledgePreview(item.title);
  const { signal } = view;
  try {
    const data = await api("/v1/agent/knowledge/materials/" + encodeURIComponent(item.id), { signal });
    if (!knowledgePreviewIsCurrent(request, signal)) return;
    byId("knowledge-preview-status").textContent = [communityKinds[data.kind], communityStates[data.state],
      data.source_id, data.keywords.length ? "关键词 " + data.keywords.join("、") : ""].filter(Boolean).join(" · ");
    knowledgeExcerpt(byId("knowledge-preview-items"), "资料正文", data.body);
  } catch (error) {
    if (knowledgePreviewIsCurrent(request, signal)) byId("knowledge-preview-status").textContent = error.message;
  }
}

byId("knowledge-search-form").addEventListener("submit", async event => {
  event.preventDefault();
  const submit = byId("knowledge-search-submit");
  if (submit.disabled) return;
  const fields = Object.fromEntries(new FormData(event.currentTarget));
  const { signal } = view;
  const results = byId("knowledge-search-results");
  const status = byId("knowledge-search-status");
  results.replaceChildren();
  status.textContent = "正在检索…";
  submit.disabled = true;
  try {
    const data = await api("/v1/agent/knowledge/search", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields), signal,
    });
    if (signal.aborted) return;
    status.textContent = data.evidence.length ? "命中 " + data.evidence.length + (data.catalog ? " 条书目" : " 条资料") : "没有找到当前身份可读的相关资料。";
    for (const item of data.evidence) {
      const heading = item.title ? item.title + " · " + (item.locator || "书名") : item.id;
      knowledgeExcerpt(results, heading, item.text);
    }
  } catch (error) {
    if (!signal.aborted) status.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

function bookCategories(id, books) {
  const select = byId(id);
  const current = select.value;
  const categories = [...new Set(books.map(book => book.category).filter(Boolean))].sort((a,b) => a.localeCompare(b,"zh-CN"));
  select.replaceChildren(new Option("全部分类", ""), ...categories.map(name => new Option(name, name)));
  if (categories.includes(current)) select.value = current;
  return select.value;
}

function bookMetadata(book) {
  return [book.author, book.isbn && "ISBN " + book.isbn, book.shelf].filter(Boolean).join(" · ");
}

function renderManagement() {
  const rows = byId("book-rows");
  const search = byId("book-search").value.trim().toLowerCase();
  const filter = byId("book-filter").value;
  const audience = byId("book-audience-filter").value;
  const category = bookCategories("admin-category", managementBooks);
  const books = managementBooks.filter(book => {
    if ((audience && book.audience !== audience) || (category && book.category !== category)) return false;
    if (![book.title, book.author, book.isbn, book.shelf, book.category].some(value => value?.toLowerCase().includes(search))) return false;
    if (filter === "published") return !book.archived_at && Boolean(book.active_version);
    return filter ? bookState(book) === filter : !book.archived_at;
  });
  rows.replaceChildren();
  byId("book-list-status").textContent = books.length ? "" :
    managementBooks.length ? "没有符合筛选条件的图书。" : "还没有登记图书。";
  for (const book of books) {
    const row = rows.insertRow();
    const bookCell = row.insertCell();
    const title = document.createElement("strong");
    const meta = document.createElement("small");
    title.textContent = book.title;
    meta.textContent = bookMetadata(book);
    bookCell.append(title, meta);
    row.insertCell().textContent = book.audience === "staff" ? "仅本馆馆员" : "本馆读者和馆员";
    const state = bookState(book);
    const badge = document.createElement("span");
    badge.className = "status-pill";
    badge.dataset.state = state;
    badge.textContent = bookStates[state];
    const status = row.insertCell();
    status.append(badge);
    if (book.active_version && state !== "published" && !book.archived_at) {
      const live = document.createElement("small");
      live.textContent = "已有版本在用";
      status.append(live);
    }
    const actions = document.createElement("div");
    actions.className = "book-actions";
    actions.append(tableAction("详情", () => openBook(book)));
    if (!book.archived_at) actions.append(tableAction("编辑", () => showBookEditor(book)));
    actions.append(tableAction(book.archived_at ? "恢复" : "删除", () => confirmBookChange(book), !book.archived_at));
    row.insertCell().append(actions);
  }
}

function tableAction(label, handler, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = danger ? "secondary danger" : "secondary";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

let portalVersion = null;
let portalRequest = 0;
const portalForm = byId("portal-admin-form");
const portalFlags = ["external_notices", "registration_enabled", "otp_login_enabled"];

async function loadPortalSettings() {
  const request = ++portalRequest;
  const { signal } = view;
  const status = byId("portal-settings-status");
  portalVersion = null;
  byId("portal-save").disabled = true;
  status.textContent = "正在读取设置…";
  try {
    const settings = await api("/v1/admin/portal", { signal });
    if (signal.aborted || request !== portalRequest) return;
    portalVersion = settings.version;
    const feed = byId("portal-notice-feed");
    const choices = settings.notice_feed_options;
    feed.replaceChildren(new Option("使用部署默认来源", ""), ...choices.map(url => new Option(url, url)));
    if (settings.notice_feed && !choices.includes(settings.notice_feed)) {
      feed.add(new Option("已失效的来源", settings.notice_feed, true, true));
    }
    feed.value = settings.notice_feed ?? "";
    byId("portal-feed-note").textContent = choices.length ? "可从本馆已批准的来源中选择。" : "部署方尚未为本馆提供可选来源。";
    for (const key of ["title", "intro"]) portalForm.elements[key].value = settings[key];
    for (const key of portalFlags) portalForm.elements[key].checked = settings[key];
    for (const input of portalForm.elements.shortcuts) input.checked = settings.shortcuts.includes(input.value);
    status.textContent = "";
    byId("portal-save").disabled = false;
  } catch (error) {
    if (!signal.aborted && request === portalRequest) status.textContent = error.message;
  }
}
portalForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (portalVersion === null || byId("portal-save").disabled) return;
  const status = byId("portal-settings-status");
  const { signal } = view;
  const request = ++portalRequest;
  const body = {
    title: portalForm.elements.title.value,
    intro: portalForm.elements.intro.value,
    version: portalVersion,
    shortcuts: new FormData(portalForm).getAll("shortcuts"),
    notice_feed: byId("portal-notice-feed").value || null,
  };
  for (const key of portalFlags) body[key] = portalForm.elements[key].checked;
  byId("portal-save").disabled = true;
  status.textContent = "正在保存…";
  try {
    const saved = await api("/v1/admin/portal", {
      method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body), signal,
    });
    if (signal.aborted || request !== portalRequest) return;
    portalVersion = saved.version;
    status.textContent = "设置已保存。";
  } catch (error) {
    if (!signal.aborted && request === portalRequest) status.textContent = error.message;
  } finally {
    if (!signal.aborted && request === portalRequest) byId("portal-save").disabled = false;
  }
});

let helpInboxRequest = 0;
async function loadHelpInbox() {
  const request = ++helpInboxRequest;
  const inbox = byId("help-inbox");
  const { signal } = view;
  inbox.textContent = "正在读取人工咨询…";
  try {
    const entries = await api("/v1/admin/help", { signal });
    if (request !== helpInboxRequest || signal.aborted) return;
    inbox.textContent = entries.length ? "" : "本馆暂无人工咨询。";
    for (const entry of entries) showHelpInboxEntry(inbox, entry, signal);
  } catch (error) {
    if (request === helpInboxRequest && !signal.aborted) inbox.textContent = error.message;
  }
}

function showHelpInboxEntry(inbox, entry, signal) {
  const card = document.createElement("article");
  const question = document.createElement("h3");
  const meta = document.createElement("p");
  const state = document.createElement("span");
  const time = document.createElement("time");
  card.className = "help-card";
  question.textContent = entry.query;
  meta.className = "help-meta field-note";
  state.className = "status-pill";
  state.dataset.state = entry.answered_at ? "published" : "ready";
  state.textContent = entry.answered_at ? "已回复" : "待回复";
  time.dateTime = entry.created_at;
  time.textContent = communityDate.format(new Date(entry.created_at));
  meta.append(state, time);
  card.append(question, meta);
  inbox.append(card);
  if (entry.answered_at) {
    const reply = document.createElement("p");
    reply.className = "help-reply";
    reply.textContent = entry.reply;
    card.append(reply);
    return;
  }
  const form = document.createElement("form");
  const label = document.createElement("label");
  const input = document.createElement("textarea");
  const send = document.createElement("button");
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  input.required = true;
  input.maxLength = 4000;
  label.textContent = "回复读者";
  label.append(input);
  send.textContent = "发送回复";
  form.append(label, send, status);
  card.append(form);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (send.disabled) return;
    send.disabled = true;
    try {
      await api("/v1/admin/help/" + encodeURIComponent(entry.job_id) + "/reply", {
        method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({reply:input.value}), signal,
      });
      if (!signal.aborted) void loadHelpInbox();
    } catch (error) {
      if (!signal.aborted) status.textContent = error.message;
    } finally {
      send.disabled = false;
    }
  });
}
byId("refresh-help").addEventListener("click", () => void loadHelpInbox());

let communityPosts = [];
let communityRequest = 0;
let editingCommunityPost = null;
let pendingCommunityState = null;

function communityApi(path = "", options = {}) {
  return api("/v1/admin/community-posts" + path, { signal: view.signal, ...options });
}

function resetCommunity() {
  communityRequest++;
  communityPosts = [];
  editingCommunityPost = pendingCommunityState = null;
  byId("community-search").value = byId("community-kind-filter").value = byId("community-state-filter").value = "";
  byId("community-action-status").textContent = byId("community-list-status").textContent = "";
  byId("community-rows").replaceChildren();
  byId("community-dialog").close();
  byId("community-state-dialog").close();
  byId("roster-dialog").close();
  byId("community-form").reset();
}

async function loadCommunity() {
  const request = ++communityRequest;
  const status = byId("community-list-status");
  status.textContent = "正在读取本馆公告与活动…";
  byId("community-rows").replaceChildren();
  communityPosts = [];
  try {
    const posts = await communityApi();
    if (request !== communityRequest) return;
    communityPosts = posts;
    renderCommunity();
  } catch (error) {
    if (request === communityRequest) status.textContent = error.message;
  }
}

function renderCommunity() {
  const search = byId("community-search").value.trim().toLowerCase();
  const kind = byId("community-kind-filter").value;
  const state = byId("community-state-filter").value;
  const posts = communityPosts.filter(post =>
    (!kind || post.kind === kind) && (!state || post.state === state) && post.title.toLowerCase().includes(search));
  const rows = byId("community-rows");
  rows.replaceChildren();
  byId("community-list-status").textContent = posts.length ? "" : communityPosts.length
    ? "没有符合筛选条件的内容。" : "本馆还没有发布内容，可新建草稿。";
  for (const post of posts) {
    const row = rows.insertRow();
    row.insertCell().textContent = post.title;
    row.insertCell().textContent = communityKinds[post.kind];
    const badge = document.createElement("span");
    badge.className = "status-pill";
    badge.dataset.state = post.state;
    badge.textContent = communityStates[post.state];
    row.insertCell().append(badge);
    row.insertCell().textContent = communityDate.format(new Date(post.updated_at));
    const actions = document.createElement("div");
    actions.className = "book-actions";
    const live = post.state === "published";
    actions.append(tableAction("编辑", () => editCommunity(post)),
      tableAction(live ? "撤回" : "发布", () => changeCommunityState(post), live));
    if (post.kind === "activity" && post.capacity) actions.append(tableAction("报名名单", () => showRoster(post)));
    row.insertCell().append(actions);
  }
}

async function showRoster(post) {
  const dialog = byId("roster-dialog");
  const list = byId("roster-list");
  const { signal } = view;
  byId("roster-title").textContent = post.title + " · 报名名单";
  list.textContent = "正在读取报名名单…";
  dialog.showModal();
  try {
    const entries = await communityApi("/" + post.id + "/enrollments");
    if (signal.aborted || byId("roster-title").textContent !== post.title + " · 报名名单") return;
    const names = document.createElement("ul");
    for (const entry of entries) {
      const item = document.createElement("li");
      item.textContent = [entry.display_name || entry.username || entry.user_id, entry.phone,
        communityDate.format(new Date(entry.enrolled_at))].filter(Boolean).join(" · ");
      names.append(item);
    }
    list.replaceChildren(names);
    if (!entries.length) list.textContent = "暂无读者报名。";
  } catch (error) {
    if (!signal.aborted) list.textContent = error.message;
  }
}

const communityForm = byId("community-form");
function syncCommunityEditor() {
  const kind = communityForm.elements.kind.value;
  byId("community-featured-group").hidden = kind !== "faq";
  const activity = byId("community-activity-fields");
  activity.hidden = activity.disabled = kind !== "activity";
}
byId("community-kind").addEventListener("change", syncCommunityEditor);

function editCommunity(post = null) {
  editingCommunityPost = post;
  byId("community-form").reset();
  byId("community-dialog-title").textContent = post ? "编辑发布内容" : "新建发布内容";
  byId("save-community-post").textContent = post?.state === "published" ? "保存并更新读者端" : "保存草稿内容";
  if (post) {
    byId("community-kind").value = post.kind;
    byId("community-post-title").value = post.title;
    byId("community-post-body").value = post.body;
    communityForm.elements.sort_order.value = post.sort_order;
    communityForm.elements.keywords.value = post.keywords.join(", ");
    communityForm.elements.featured.checked = post.featured;
    communityForm.elements.location.value = post.location ?? "";
    communityForm.elements.capacity.value = post.capacity ?? "";
    if (post.starts_at) {
      const start = new Date(post.starts_at);
      const local = new Date(start.getTime() - start.getTimezoneOffset() * 60000);
      communityForm.elements.starts_at.value = local.toISOString().slice(0, 16);
    }
  }
  byId("community-kind").disabled = Boolean(post?.source_id);
  syncCommunityEditor();
  authError("community-form-error");
  byId("community-dialog").showModal();
  byId("community-post-title").focus();
}

function changeCommunityState(post) {
  pendingCommunityState = post;
  const live = post.state === "published";
  const label = live ? "撤回" : "发布";
  byId("community-state-title").textContent = label + "《" + post.title + "》？";
  byId("community-state-note").textContent = live
    ? (post.kind === "handbook" ? "撤回后，新问答将停止检索这份手册。" : "撤回后，内容将从读者端移除。")
    : (post.kind === "handbook" ? "手册发布后只参与本馆馆员的问答检索。" : "发布后，本馆读者可以查看。");
  byId("save-community-state").textContent = "确认" + label;
  authError("community-state-error");
  byId("community-state-dialog").showModal();
  byId("cancel-community-state").focus();
}

byId("community-search").addEventListener("input", renderCommunity);
byId("community-kind-filter").addEventListener("change", renderCommunity);
byId("community-state-filter").addEventListener("change", renderCommunity);
byId("add-community-post").addEventListener("click", () => editCommunity());
byId("cancel-community-post").addEventListener("click", () => byId("community-dialog").close());
byId("cancel-community-state").addEventListener("click", () => byId("community-state-dialog").close());

async function saveCommunity(dialog, button, errorId, path, method, body, message) {
  if (button.disabled) return;
  const controls = [...dialog.querySelectorAll("button, input, select, textarea")].filter(control => !control.disabled);
  for (const control of controls) control.disabled = true;
  const preventCancel = event => event.preventDefault();
  dialog.addEventListener("cancel", preventCancel);
  authError(errorId);
  const { signal } = view;
  try {
    await communityApi(path, { method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
    if (signal.aborted) return;
    dialog.close();
    byId("community-action-status").textContent = message;
    await loadCommunity();
  } catch (error) {
    if (!signal.aborted) {
      authError(errorId, error.message);
      void loadCommunity();
    }
  } finally {
    for (const control of controls) control.disabled = false;
    dialog.removeEventListener("cancel", preventCancel);
  }
}

byId("community-form").addEventListener("submit", event => {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(event.currentTarget));
  fields.kind ??= editingCommunityPost?.kind;
  fields.title = fields.title.trim();
  fields.body = fields.body.trim();
  fields.sort_order = Number(fields.sort_order);
  fields.keywords = fields.keywords.split(/[,，]/).map(value => value.trim()).filter(Boolean);
  fields.featured = fields.kind === "faq" && communityForm.elements.featured.checked;
  if (fields.kind === "activity") {
    fields.starts_at = fields.starts_at ? new Date(fields.starts_at).toISOString() : null;
    fields.location = fields.location.trim() || null;
    fields.capacity = fields.capacity ? Number(fields.capacity) : null;
  }
  if (!(fields.title && fields.body)) {
    authError("community-form-error", "请填写标题和正文。");
    return;
  }
  const post = editingCommunityPost;
  if (post) fields.version = post.version;
  void saveCommunity(byId("community-dialog"), byId("save-community-post"), "community-form-error",
    post ? "/" + post.id : "", post ? "PATCH" : "POST", fields, post ? "已保存修改。" : "已新建草稿。");
});

byId("save-community-state").addEventListener("click", () => {
  const post = pendingCommunityState;
  const state = post.state === "published" ? "withdrawn" : "published";
  void saveCommunity(byId("community-state-dialog"), byId("save-community-state"), "community-state-error",
    "/" + post.id + "/state", "POST", { version: post.version, state },
    state === "published" ? "已发布。" : "已撤回。");
});

function showBookEditor(book = null) {
  editingBook = book;
  const form = byId("book-form");
  form.reset();
  byId("book-dialog-title").textContent = book ? "编辑图书信息" : "新增图书";
  byId("book-submit").textContent = book ? "保存修改" : "新增图书";
  byId("book-status").textContent = "";
  if (book) {
    for (const key of ["title", "author", "isbn", "shelf", "category", "audience"]) form.elements[key].value = book[key] ?? "";
  }
  bookDialog.showModal();
}

function confirmBookChange(book) {
  archiveTarget = book;
  const restoring = Boolean(book.archived_at);
  const label = restoring ? "恢复图书" : "删除图书";
  byId("book-archive-title").textContent = label;
  byId("confirm-book-archive").textContent = label;
  byId("confirm-book-archive").classList.toggle("danger-solid", !restoring);
  byId("book-archive-description").textContent = restoring
    ? "恢复《" + book.title + "》后，会按原可见范围重新进入馆藏和检索。"
    : "删除《" + book.title + "》后，将从馆藏和后续检索中移除。已生成的历史引用保留。";
  byId("book-archive-status").textContent = "";
  bookArchiveDialog.showModal();
}

byId("edit-selected-book").addEventListener("click", () => showBookEditor(selectedManagementBook));
byId("archive-selected-book").addEventListener("click", () => {
  if (selectedManagementBook) confirmBookChange(selectedManagementBook);
});
byId("cancel-book-archive").addEventListener("click", () => bookArchiveDialog.close());
bookArchiveDialog.addEventListener("cancel", event => {
  if (byId("confirm-book-archive").disabled) event.preventDefault();
});
byId("confirm-book-archive").addEventListener("click", async () => {
  const book = archiveTarget;
  const confirm = byId("confirm-book-archive");
  const cancel = byId("cancel-book-archive");
  if (!book || confirm.disabled) return;
  const restoring = Boolean(book.archived_at);
  const path = "/v1/admin/books/" + encodeURIComponent(book.id) + (restoring ? "/restore" : "") +
    "?revision=" + encodeURIComponent(book.revision);
  const signal = view.signal;
  confirm.disabled = cancel.disabled = true;
  try {
    await api(path, { method: restoring ? "POST" : "DELETE", signal });
    if (signal.aborted) return;
    bookArchiveDialog.close();
    byId("book-filter").value = restoring ? "" : "archived";
    if (!byId("book-detail").hidden) byId("back-to-books").click();
    else await loadManagement();
    byId("book-action-notice").textContent = restoring ? "已恢复《" + book.title + "》。" : "已删除《" + book.title + "》。";
  } catch (error) {
    if (!signal.aborted) {
      byId("book-archive-status").textContent = error.message;
      void loadManagement();
    }
  } finally {
    if (!signal.aborted) confirm.disabled = cancel.disabled = false;
  }
});

function openBook(book) {
  selectedManagementBook = book;
  byId("book-archive-note").hidden = !book.archived_at;
  byId("edit-selected-book").hidden = Boolean(book.archived_at);
  byId("archive-selected-book").textContent = book.archived_at ? "恢复图书" : "删除图书";
  byId("managed-book").value = book.id;
  byId("book-list").hidden = true;
  byId("book-detail").hidden = false;
  byId("book-detail-title").textContent = book.title;
  byId("book-detail-meta").textContent = [bookMetadata(book),
    book.audience === "staff" ? "仅本馆馆员可见" : "本馆读者和馆员可见"].filter(Boolean).join(" · ");
  byId("book-detail-title").focus({ preventScroll: true });
  byId("upload-form").reset();
  byId("upload-form").hidden = Boolean(book.archived_at);
  byId("upload-status").textContent = "";
  byId("versions").replaceChildren();
  selectedSnapshot = undefined;
  void showVersions(book.id);
}

async function loadManagement(selectId) {
  const signal = view.signal;
  const request = ++managementRequest;
  const status = byId("book-list-status");
  status.textContent = "正在读取馆藏…";
  try {
    const books = await api("/v1/admin/books", { signal });
    if (signal.aborted || request !== managementRequest || byId("management-view").hidden) return;
    managementBooks = books;
    renderManagement();
    const book = books.find(item => item.id === selectId);
    if (book) openBook(book);
    else if (selectId) status.textContent = "该图书在本馆不可用。";
    else if (!byId("book-detail").hidden) {
      const current = books.find(item => item.id === byId("managed-book").value);
      if (current) openBook(current);
    }
  } catch (error) {
    if (!signal.aborted && request === managementRequest) status.textContent = error.message;
  }
}

byId("book-search").addEventListener("input", renderManagement);
byId("book-filter").addEventListener("change", renderManagement);
byId("admin-category").addEventListener("change", renderManagement);
byId("book-audience-filter").addEventListener("change", renderManagement);
byId("back-to-books").addEventListener("click", () => {
  clearTimeout(versionPoll);
  const url = new URL(location.href);
  url.searchParams.delete("book");
  url.searchParams.delete("version");
  history.replaceState(null, "", url);
  reviewTarget = null;
  selectedManagementBook = null;
  byId("managed-book").value = "";
  byId("book-detail").hidden = true;
  byId("book-list").hidden = false;
  byId("book-search").focus();
  void loadManagement();
});

byId("add-book").addEventListener("click", () => showBookEditor());
byId("close-book-dialog").addEventListener("click", () => bookDialog.close());
bookDialog.addEventListener("cancel", event => {
  if (byId("book-submit").disabled) event.preventDefault();
});

function boundSelection(bookId, signal) {
  return !signal.aborted && !byId("management-view").hidden && byId("managed-book").value === bookId;
}

async function showVersions(bookId) {
  clearTimeout(versionPoll);
  const signal = view.signal;
  try {
    const versions = await api("/v1/books/" + encodeURIComponent(bookId) + "/uploads", { signal });
    if (!boundSelection(bookId, signal)) return;
    const snapshot = JSON.stringify([bookId, versions]);
    if (selectedSnapshot !== snapshot) {
      const versionsPanel = byId("versions");
      if (!versions.length) versionsPanel.textContent = "还没有电子版。上传后可查看解析状态。";
      else byId("versions").replaceChildren(...versions.map(version => renderVersion(bookId, version)));
      selectedSnapshot = snapshot;
      if (reviewTarget) {
        const target = document.getElementById("version-" + reviewTarget);
        reviewTarget = null;
        target?.focus();
      }
    }
    if (versions.some(version => ["queued", "running"].includes(version.status))) {
      versionPoll = setTimeout(() => {
        if (boundSelection(bookId, signal)) void showVersions(bookId);
      }, 900);
    }
  } catch (error) {
    if (boundSelection(bookId, signal)) byId("upload-status").textContent = error.message;
  }
}

function renderVersion(bookId, version) {
  const card = document.createElement("article");
  card.id = "version-" + version.job_id;
  card.tabIndex = -1;
  const name = document.createElement("strong");
  const status = document.createElement("p");
  const labels = { queued: "排队中", running: "解析中", ready: "待审核发布", failed: "解析失败" };
  name.textContent = version.filename;
  status.textContent = version.active ? "当前发布版本" : labels[version.status] ?? version.status;
  if (version.error_code) {
    const messages = {
      no_extractable_text: "文件没有可提取的正文。请上传带文本层的 PDF 或其他文本格式。",
      document_limit: "正文超过处理上限。请拆分文件后上传。",
      file_unavailable: "读取原文件失败。",
    };
    status.textContent += " · " + (messages[version.error_code] ?? version.error_code);
  }
  card.append(name, status);
  if (!selectedManagementBook?.archived_at && version.status === "failed" && version.error_code === "file_unavailable") {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary";
    retry.textContent = "重试解析";
    card.append(retry);
    retry.addEventListener("click", async () => {
      if (retry.disabled) return;
      const signal = view.signal;
      retry.disabled = true;
      try {
        await api("/v1/books/" + encodeURIComponent(bookId) + "/versions/" +
          encodeURIComponent(version.job_id) + "/retry", { method: "POST", signal });
        if (boundSelection(bookId, signal)) byId("upload-status").textContent = "文件已重新排队解析。";
      } catch (error) {
        if (boundSelection(bookId, signal)) byId("upload-status").textContent = error.message;
      } finally {
        retry.disabled = false;
        if (boundSelection(bookId, signal)) void showVersions(bookId);
      }
    });
  }
  if (version.status === "ready") {
    const preview = document.createElement("details");
    const summary = document.createElement("summary");
    const content = document.createElement("div");
    summary.textContent = "预览解析内容";
    preview.append(summary, content);
    card.append(preview);
    preview.addEventListener("toggle", async () => {
      if (!preview.open || preview.dataset.loaded || preview.dataset.loading) return;
      preview.dataset.loading = "true";
      content.textContent = "正在读取前 8 个片段…";
      const signal = view.signal;
      try {
        const fragments = await api("/v1/books/" + encodeURIComponent(bookId) + "/versions/" +
          encodeURIComponent(version.job_id) + "/preview", { signal });
        if (!boundSelection(bookId, signal)) return;
        content.replaceChildren();
        for (const fragment of fragments) {
          const heading = document.createElement("strong");
          const excerpt = document.createElement("p");
          heading.textContent = fragment.locator;
          excerpt.textContent = fragment.text;
          content.append(heading, excerpt);
        }
        if (!fragments.length) content.textContent = "没有可预览的片段。";
        preview.dataset.loaded = "true";
      } catch (error) {
        if (boundSelection(bookId, signal)) content.textContent = error.message;
      } finally {
        delete preview.dataset.loading;
      }
    });
  }
  if (!selectedManagementBook?.archived_at && version.status === "ready" && !version.active) {
    const form = document.createElement("form");
    const label = document.createElement("label");
    const rights = document.createElement("input");
    const publish = document.createElement("button");
    label.textContent = "使用依据";
    rights.required = true;
    rights.maxLength = 500;
    rights.placeholder = "例如：本馆原创资料";
    publish.textContent = "发布此版本";
    label.append(rights);
    form.append(label, publish);
    card.append(form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const basis = rights.value.trim();
      if (!basis || publish.disabled) return;
      const signal = view.signal;
      publish.disabled = true;
      try {
        const path = "/v1/books/" + encodeURIComponent(bookId) + "/versions/" + encodeURIComponent(version.job_id) + "/publish";
        await api(path, { method: "POST", body: new URLSearchParams({ rights_basis: basis }), signal });
        if (boundSelection(bookId, signal)) {
          byId("upload-status").textContent = "该版本已发布，可以用于馆内问答。";
          await showVersions(bookId);
        }
      } catch (error) {
        if (boundSelection(bookId, signal)) byId("upload-status").textContent = error.message;
      } finally {
        publish.disabled = false;
      }
    });
  }
  return card;
}

byId("book-form").addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries([...new FormData(event.target)].map(([key, value]) => [key, value.trim()]));
  if (!values.title || byId("book-submit").disabled) return;
  const signal = view.signal;
  const edited = editingBook;
  const body = Object.fromEntries(Object.entries(values).filter(([, value]) => value));
  if (edited) {
    body.revision = edited.revision;
    body.category = values.category || null;
  }
  byId("book-submit").disabled = byId("close-book-dialog").disabled = true;
  try {
    const path = edited ? "/v1/admin/books/" + encodeURIComponent(edited.id) : "/v1/books";
    const { book_id } = await api(path, {
      method: edited ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    });
    if (signal.aborted) return;
    event.target.reset();
    bookDialog.close();
    await loadManagement(book_id);
    byId("book-action-notice").textContent = edited ? "已保存图书信息。" : "已新增图书。";
  } catch (error) {
    if (!signal.aborted) {
      byId("book-status").textContent = error.message;
      if (edited) void loadManagement();
    }
  } finally {
    if (!signal.aborted) byId("book-submit").disabled = byId("close-book-dialog").disabled = false;
  }
});

byId("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  const bookId = byId("managed-book").value;
  if (!bookId || byId("upload-submit").disabled) return;
  const signal = view.signal;
  byId("upload-submit").disabled = true;
  try {
    await api("/v1/books/" + encodeURIComponent(bookId) + "/uploads", {
      method: "POST", body: new FormData(event.target), signal,
    });
    if (boundSelection(bookId, signal)) {
      event.target.reset();
      byId("upload-status").textContent = "已接收电子书，解析完成后可审核发布。";
      await showVersions(bookId);
    }
  } catch (error) {
    if (boundSelection(bookId, signal)) byId("upload-status").textContent = error.message;
  } finally {
    if (!signal.aborted) byId("upload-submit").disabled = false;
  }
});


let selectedAccount;
const accountDialog = byId("account-dialog");

function showAccountEditor(row = null) {
  const form = byId("account-form");
  selectedAccount = row;
  form.reset();
  byId("account-dialog-title").textContent = row ? "编辑账号" : "新增账号";
  byId("account-phone").value = row?.phone ?? "";
  byId("account-phone").required = !row;
  byId("account-phone").readOnly = Boolean(row);
  byId("account-phone").placeholder = row && !row.phone ? "仅 API 凭据" : "";
  byId("account-role").value = row?.role ?? "reader";
  byId("account-active-field").hidden = !row;
  byId("account-active").value = String(row?.active ?? true);
  byId("account-form-message").textContent = "";
  byId("save-account").disabled = false;
  accountDialog.showModal();
}

let accountItems = [];

function renderAccounts(note = "") {
  const status = byId("accounts-status");
  const rows = byId("account-rows");
  const query = byId("account-search").value.trim();
  const role = byId("account-filter-role").value;
  const active = byId("account-filter-state").value;
  const items = accountItems.filter(item => (!query || item.phone?.includes(query)) &&
    (!role || item.role === role) && (!active || String(item.active) === active));
  rows.replaceChildren();
  status.textContent = note || (items.length ? "" : accountItems.length ? "没有符合条件的账号。" : "当前没有账号。");
  for (const item of items) {
    const row = rows.insertRow();
    const own = item.user_id === account.user_id;
    row.insertCell().textContent = (item.phone ?? "仅 API 凭据") + (own ? "（当前账号）" : "");
    row.insertCell().textContent = item.role === "staff" ? "管理员" : "读者";
    const badge = document.createElement("span");
    badge.className = "account-state" + (item.active ? " active" : "");
    badge.textContent = item.active ? "启用" : "停用";
    row.insertCell().append(badge);
    const action = row.insertCell();
    if (own) {
      action.textContent = "由其他管理员管理";
    } else {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = "编辑账号";
      button.addEventListener("click", () => showAccountEditor(item));
      action.append(button);
    }
  }
}

async function loadAccounts(note = "") {
  const signal = view.signal;
  const status = byId("accounts-status");
  status.textContent = "正在读取账号…";
  byId("accounts-scope").textContent = (tenantNames[account.tenant_id] ?? account.tenant_id) + " · 新账号归入当前馆。";
  try {
    const items = await api("/v1/admin/accounts", { signal });
    if (signal.aborted) return;
    accountItems = items;
    renderAccounts(note);
  } catch (error) {
    if (!signal.aborted) {
      accountItems = [];
      byId("account-rows").replaceChildren();
      status.textContent = error.message;
    }
  }
}

byId("account-search").addEventListener("input", () => renderAccounts());
byId("account-filter-role").addEventListener("change", () => renderAccounts());
byId("account-filter-state").addEventListener("change", () => renderAccounts());
byId("add-account").addEventListener("click", () => showAccountEditor());
byId("close-account-dialog").addEventListener("click", () => accountDialog.close());
byId("account-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = byId("save-account");
  if (button.disabled) return;
  const edited = selectedAccount;
  const body = { role: byId("account-role").value };
  let path = "/v1/admin/accounts";
  if (edited) {
    path += "/" + encodeURIComponent(edited.user_id);
    body.active = byId("account-active").value === "true";
    body.version = edited.version;
  } else body.phone = byId("account-phone").value.trim();
  const signal = view.signal;
  button.disabled = byId("close-account-dialog").disabled = true;
  try {
    await api(path, {
      method: edited ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    });
    if (signal.aborted) return;
    accountDialog.close();
    await loadAccounts(edited ? "账号已更新。" : "账号已新增，可使用对应登录入口。");
  } catch (error) {
    if (!signal.aborted) byId("account-form-message").textContent = error.message;
  } finally {
    button.disabled = byId("close-account-dialog").disabled = false;
  }
});
accountDialog.addEventListener("cancel", event => {
  if (byId("save-account").disabled) event.preventDefault();
});

const modelPath = "/v1/admin/models";
let modelProviders = [];
let selectedProvider;
let providerCatalog = [];
let selectedModels = new Map();
let providerLoad = 0;

function providerRoute(provider = selectedProvider) {
  return modelPath + "/providers/" + encodeURIComponent(provider);
}

function providerMark(name) {
  return name.trim().charAt(0).toUpperCase();
}

function setProviderCredentialPanel(open) {
  const form = byId("provider-credentials");
  form.hidden = !open;
  byId("toggle-provider-credentials").setAttribute("aria-expanded", open);
  if (open) byId("model-api-key").focus();
  else form.reset();
}

function showModelPane(added) {
  byId("tab-model-catalog").setAttribute("aria-pressed", !added);
  byId("tab-model-added").setAttribute("aria-pressed", added);
  byId("model-catalog-pane").hidden = added;
  byId("model-added-pane").hidden = !added;
}

function renderProviders() {
  const needle = byId("provider-search").value.trim().toLowerCase();
  byId("model-providers").replaceChildren(...modelProviders
    .filter(item => (item.name + " " + item.id).toLowerCase().includes(needle))
    .map(item => {
      const button = document.createElement("button");
      button.type = "button";
      const mark = document.createElement("span");
      mark.className = "provider-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = providerMark(item.name);
      const label = document.createElement("span");
      label.textContent = item.name;
      button.append(mark, label);
      button.setAttribute("aria-pressed", item.id === selectedProvider);
      button.addEventListener("click", () => void loadModels(item.id));
      return button;
    }));
}

function modelKind(kind) {
  return kind === "embedding" ? "向量嵌入" : "对话";
}

function modelDisplay(item) {
  return item.display_name === item.model_name
    ? item.model_name : item.display_name + " · " + item.model_name;
}

function renderCatalog() {
  const needle = byId("model-search").value.trim().toLowerCase();
  const kind = byId("model-filter").value;
  const shown = providerCatalog.filter(item => modelDisplay(item).toLowerCase().includes(needle) &&
    (kind === "all" || kind === item.kind));
  const list = byId("model-catalog");
  list.replaceChildren();
  byId("model-catalog-count").textContent = providerCatalog.filter(item => !item.added).length;
  if (!shown.length) {
    const empty = document.createElement("p");
    empty.className = "model-empty";
    empty.textContent = providerCatalog.length ? "没有匹配的模型。" : "暂无目录。可尝试获取供应商模型列表。";
    list.append(empty);
  }
  for (const item of shown) {
    const key = JSON.stringify([item.model_name, item.kind]);
    const option = document.createElement("label");
    option.className = "model-catalog-option";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = selectedModels.has(key);
    check.disabled = item.added;
    const title = document.createElement("span");
    title.className = "model-title-text";
    title.textContent = item.display_name;
    if (item.display_name !== item.model_name) {
      const apiId = document.createElement("small");
      apiId.className = "model-api-id";
      apiId.textContent = item.model_name;
      title.append(apiId);
    }
    const metadata = document.createElement("span");
    metadata.className = "model-type-labels";
    for (const text of [modelKind(item.kind), item.added ? "已添加" : item.source === "discovered" ? "供应商目录" : "预设"]) {
      const badge = document.createElement("small");
      badge.textContent = text;
      metadata.append(badge);
    }
    option.append(check, title, metadata);
    list.append(option);
    check.addEventListener("change", () => {
      if (check.checked) selectedModels.set(key, {model_name: item.model_name, kind: item.kind});
      else selectedModels.delete(key);
      updateAddButton();
    });
  }
  updateAddButton();
}

function updateAddButton() {
  const button = byId("add-selected-models");
  button.disabled = selectedModels.size === 0 || selectedModels.size > 30;
  button.textContent = selectedModels.size ? "添加所选模型（" + selectedModels.size + "）" : "添加所选模型";
}

function renderModelRows(items, active, signal) {
  const rows = byId("model-rows");
  byId("model-list-table").hidden = !items.length;
  byId("model-list-empty").hidden = Boolean(items.length);
  byId("model-added-count").textContent = items.length;
  rows.replaceChildren();
  for (const item of items) {
    const row = rows.insertRow();
    row.insertCell().textContent = modelDisplay(item);
    row.insertCell().textContent = modelKind(item.kind) + (item.dimension ? " · " + item.dimension + " 维" : "");
    const status = document.createElement("span");
    status.className = "status-pill";
    status.dataset.state = item.verified_at ? "published" : "ready";
    status.textContent = item.id === active
      ? (item.verified_at ? "使用中" : "已绑定 · 待验证") : item.verified_at ? "可用" : "待验证";
    row.insertCell().append(status);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = "测试连接";
    row.insertCell().append(button);
    button.addEventListener("click", async () => {
      button.disabled = true;
      await modelAction(async () => {
        await api(modelPath + "/" + encodeURIComponent(item.id) + "/verify", { method: "POST", signal });
        return item.model_name + " 已通过测试。";
      });
      button.disabled = false;
    });
  }
}

async function loadModels(provider, note = "") {
  const signal = view.signal;
  const ticket = ++providerLoad;
  byId("models-status").textContent = "正在加载…";
  setProviderCredentialPanel(false);
  byId("toggle-provider-credentials").disabled = true;
  byId("discover-models").disabled = true;
  byId("add-selected-models").disabled = true;
  if (provider && provider !== selectedProvider) {
    providerCatalog = [];
    selectedModels.clear();
    byId("model-search").value = "";
    byId("model-filter").value = "all";
    showModelPane(false);
    renderCatalog();
    renderModelRows([], null, signal);
  }
  try {
    const providers = modelProviders.length ? modelProviders : await api(modelPath + "/providers", { signal });
    if (signal.aborted || ticket !== providerLoad) return;
    modelProviders = providers;
    selectedProvider = provider ?? selectedProvider ?? providers[0].id;
    renderProviders();
    const providerName = providers.find(item => item.id === selectedProvider)?.name ?? selectedProvider;
    byId("current-provider").textContent = providerName;
    byId("provider-mark").textContent = providerMark(providerName);
    const [catalog, items, settings] = await Promise.all([
      api(providerRoute() + "/catalog", { signal }), api(modelPath, { signal }), api("/v1/agent/settings", { signal }),
    ]);
    if (signal.aborted || ticket !== providerLoad) return;
    providerCatalog = catalog.models;
    byId("provider-key-state").textContent = catalog.has_key ? "已配置供应商凭据" : "尚未配置供应商凭据";
    byId("provider-key-state").classList.toggle("connected", catalog.has_key);
    const credentialButton = byId("toggle-provider-credentials");
    credentialButton.disabled = false;
    credentialButton.textContent = catalog.has_key ? "更换凭据" : "配置凭据";
    byId("discover-models").disabled = !catalog.has_key && selectedProvider !== "custom";
    renderCatalog();
    renderModelRows(items.filter(item => item.provider === selectedProvider), settings.answer_model, signal);
    byId("models-status").textContent = note;
  } catch (error) {
    if (!signal.aborted && ticket === providerLoad) byId("models-status").textContent = error.message;
  }
}

async function modelAction(operation) {
  const provider = selectedProvider;
  const signal = view.signal;
  const status = byId("models-status");
  status.textContent = "正在处理…";
  try {
    const note = await operation(provider, signal);
    if (!signal.aborted && provider === selectedProvider) await loadModels(provider, note);
  } catch (error) {
    if (!signal.aborted && provider === selectedProvider) status.textContent = error.message;
  }
}

byId("open-models").addEventListener("click", () => switchView("models", true));
byId("go-agent-model").addEventListener("click", () => switchView("agent", true));
byId("provider-search").addEventListener("input", renderProviders);
byId("tab-model-catalog").addEventListener("click", () => showModelPane(false));
byId("tab-model-added").addEventListener("click", () => showModelPane(true));
byId("toggle-provider-credentials").addEventListener("click", () => {
  setProviderCredentialPanel(byId("provider-credentials").hidden);
});
byId("model-search").addEventListener("input", renderCatalog);
byId("model-filter").addEventListener("change", renderCatalog);
byId("provider-credentials").addEventListener("submit", async event => {
  event.preventDefault();
  const field = byId("model-api-key");
  await modelAction(async (provider, signal) => {
    await api(providerRoute(provider) + "/credentials", {
      method: "PUT", signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: field.value }),
    });
    return "凭据已保存，可以获取目录或勾选预设模型。";
  });
  field.value = "";
});
byId("discover-models").addEventListener("click", async () => {
  const button = byId("discover-models");
  button.disabled = true;
  await modelAction(async (provider, signal) => {
    const result = await api(providerRoute(provider) + "/discover", { method: "POST", signal });
    return "已从供应商筛出 " + result.count + " 个可选模型。";
  });
  if (byId("provider-key-state").textContent === "已配置供应商凭据" || selectedProvider === "custom") button.disabled = false;
});
byId("add-selected-models").addEventListener("click", async () => {
  const button = byId("add-selected-models");
  if (selectedProvider !== "custom" && !byId("provider-key-state").classList.contains("connected")) {
    byId("models-status").textContent = "请先填写供应商 API Key，再添加模型。";
    setProviderCredentialPanel(true);
    return;
  }
  button.disabled = true;
  await modelAction(async (provider, signal) => {
    const created = await api(providerRoute(provider) + "/models", {
      method: "POST", signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ models: [...selectedModels.values()] }),
    });
    if (provider === selectedProvider) {
      selectedModels.clear();
      showModelPane(true);
    }
    return "已添加 " + created.length + " 个模型。通过测试后可在 Agent 配置里选择。";
  });
  updateAddButton();
});

if (location.pathname !== "/") {
  api("/v1/me").then(showWorkspace).catch(() => {
    if (!adminViews.has(location.pathname)) history.replaceState(null, "", "/");
  });
}
