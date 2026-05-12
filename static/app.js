const state = {
  currentCategory: null,
  settings: null,
  models: [],
  chat: [],
  chatModel: "",
  review: null,
};

const view = document.querySelector("#view");
const liveRegion = document.querySelector("#live-region");
const statusRegion = document.querySelector("#status-region");
const dialog = document.querySelector("#name-dialog");
const dialogForm = document.querySelector("#name-form");
const dialogTitle = document.querySelector("#dialog-title");
const dialogName = document.querySelector("#dialog-name");
const dialogCancel = document.querySelector("#dialog-cancel");

function announce(text, options = {}) {
  if (statusRegion) {
    statusRegion.hidden = !text;
    statusRegion.textContent = text || "";
    statusRegion.classList.toggle("error", Boolean(options.error));
  }
  if (options.speak) {
    liveRegion.textContent = text || "";
  }
}

function showError(error) {
  const message = error?.message || String(error || "حدث خطأ غير متوقع");
  announce(message, { speak: true, error: true });
  alert(message);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = "حدث خطأ غير متوقع";
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function setActiveNav(page) {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === page);
  });
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function openNameDialog(title, initialValue = "") {
  return new Promise((resolve) => {
    dialogTitle.textContent = title;
    dialogName.value = initialValue;
    dialog.showModal();
    dialogName.focus();

    const cleanup = () => {
      dialogForm.removeEventListener("submit", submit);
      dialogCancel.removeEventListener("click", cancel);
      dialog.removeEventListener("close", close);
    };
    const submit = (event) => {
      event.preventDefault();
      const value = dialogName.value.trim();
      if (!value) return;
      cleanup();
      dialog.close();
      resolve(value);
    };
    const cancel = () => {
      cleanup();
      dialog.close();
      resolve(null);
    };
    const close = () => {
      cleanup();
      resolve(null);
    };
    dialogForm.addEventListener("submit", submit);
    dialogCancel.addEventListener("click", cancel);
    dialog.addEventListener("close", close, { once: true });
  });
}

function route() {
  liveRegion.textContent = "";
  announce("");
  const hash = window.location.hash || "#/categories";
  const parts = hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "settings") return renderSettings();
  if (parts[0] === "companion") return renderCompanion();
  if (parts[0] === "review" && parts[1]) return renderReview(Number(parts[1]));
  if (parts[0] === "categories" && parts[1]) return renderCategory(Number(parts[1]));
  return renderCategoriesRoot();
}

async function renderCategoriesRoot() {
  setActiveNav("categories");
  state.currentCategory = null;
  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>الأقسام</h1>
        <p class="subtle">اختر قسما أو ابدأ مراجعة شاملة لما بداخله.</p>
      </div>
      <button class="primary" id="add-root">إضافة قسم</button>
    </div>
    <div id="category-list" class="grid-list" aria-live="polite"></div>
  `;
  document.querySelector("#add-root").addEventListener("click", async () => {
    const name = await openNameDialog("إضافة قسم جديد");
    if (!name) return;
    await api("/api/categories", { method: "POST", body: JSON.stringify({ name }) });
    announce("تمت إضافة القسم");
    renderCategoriesRoot();
  });
  const categories = await api("/api/categories?root=true");
  renderCategoryList(categories, document.querySelector("#category-list"));
}

function renderCategoryList(categories, container) {
  if (!categories.length) {
    container.innerHTML = `<div class="empty-state">لا توجد أقسام بعد.</div>`;
    return;
  }
  container.innerHTML = categories
    .map((category) => {
      const meta = category.parent_id === null
        ? `${category.children_count || 0} قسم فرعي`
        : `${category.cards_count || 0} بطاقة`;
      return `
        <article class="item-card">
          <header>
            <div class="item-main">
              <a class="item-title" href="#/categories/${category.id}">${escapeHtml(category.name)}</a>
              <span class="subtle">${meta}</span>
            </div>
            <div class="row-actions" aria-label="إجراءات ${escapeHtml(category.name)}">
              <a class="button-link" href="#/review/${category.id}">بدء المراجعة</a>
              <button class="compact" data-action="rename" data-id="${category.id}" data-name="${escapeHtml(category.name)}">إعادة تسمية</button>
              <button class="compact danger" data-action="delete" data-id="${category.id}">حذف</button>
            </div>
          </header>
        </article>
      `;
    })
    .join("");

  container.querySelectorAll("[data-action='rename']").forEach((button) => {
    button.addEventListener("click", async () => {
      const name = await openNameDialog("إعادة تسمية القسم", button.dataset.name);
      if (!name) return;
      await api(`/api/categories/${button.dataset.id}`, { method: "PATCH", body: JSON.stringify({ name }) });
      announce("تم تعديل اسم القسم");
      route();
    });
  });

  container.querySelectorAll("[data-action='delete']").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!confirm("سيتم حذف القسم وما بداخله. هل تريد المتابعة؟")) return;
      await api(`/api/categories/${button.dataset.id}`, { method: "DELETE" });
      announce("تم حذف القسم");
      route();
    });
  });
}

async function renderCategory(id) {
  setActiveNav("categories");
  const category = await api(`/api/categories/${id}`);
  state.currentCategory = category;
  if (category.parent_id === null) {
    const children = await api(`/api/categories?parent_id=${id}`);
    view.innerHTML = `
      <nav class="crumbs" aria-label="المسار">
        <a href="#/categories">الأقسام</a>
        <span aria-hidden="true">/</span>
        <span>${escapeHtml(category.name)}</span>
      </nav>
      <div class="page-head">
        <div>
          <h1>${escapeHtml(category.name)}</h1>
          <p class="subtle">الأقسام الفرعية داخل هذا القسم.</p>
        </div>
        <div class="toolbar">
          <a class="button-link primary" href="#/review/${category.id}">بدء المراجعة</a>
          <button id="add-child">إضافة قسم فرعي</button>
        </div>
      </div>
      <div id="category-list" class="grid-list"></div>
    `;
    document.querySelector("#add-child").addEventListener("click", async () => {
      const name = await openNameDialog("إضافة قسم فرعي");
      if (!name) return;
      await api("/api/categories", { method: "POST", body: JSON.stringify({ name, parent_id: id }) });
      announce("تمت إضافة القسم الفرعي");
      renderCategory(id);
    });
    renderCategoryList(children, document.querySelector("#category-list"));
    return;
  }
  renderLeafCategory(category);
}

async function renderLeafCategory(category) {
  const cards = await api(`/api/categories/${category.id}/cards`);
  view.innerHTML = `
    <nav class="crumbs" aria-label="المسار">
      <a href="#/categories">الأقسام</a>
      <span aria-hidden="true">/</span>
      <a href="#/categories/${category.parent_id}">القسم الرئيسي</a>
      <span aria-hidden="true">/</span>
      <span>${escapeHtml(category.name)}</span>
    </nav>
    <div class="page-head">
      <div>
        <h1>${escapeHtml(category.name)}</h1>
        <p class="subtle">${cards.length} بطاقة</p>
      </div>
      <a class="button-link primary" href="#/review/${category.id}">بدء المراجعة</a>
    </div>
    <section class="panel upload-box" aria-labelledby="upload-title">
      <h2 id="upload-title">رفع بطاقات JSON</h2>
      <form id="upload-form" class="field-stack">
        <div class="field">
          <label for="cards-file">ملف البطاقات</label>
          <input id="cards-file" name="file" type="file" accept="application/json,.json" required />
        </div>
        <div class="form-actions">
          <button class="primary" type="submit">رفع البطاقات</button>
        </div>
      </form>
    </section>
    <section aria-labelledby="cards-title">
      <h2 id="cards-title">البطاقات</h2>
      <div id="cards-list"></div>
    </section>
  `;
  document.querySelector("#upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = document.querySelector("#cards-file").files[0];
    const data = new FormData();
    data.append("file", file);
    const result = await api(`/api/categories/${category.id}/cards/import`, { method: "POST", body: data });
    announce(`تم رفع ${result.imported} بطاقة`);
    renderLeafCategory(category);
  });
  renderCards(cards);
}

function renderCards(cards) {
  const container = document.querySelector("#cards-list");
  if (!cards.length) {
    container.innerHTML = `<div class="empty-state">لا توجد بطاقات في هذا القسم.</div>`;
    return;
  }
  container.innerHTML = `
    <table class="cards-table">
      <thead>
        <tr>
          <th scope="col">السؤال</th>
          <th scope="col">الإجابة</th>
          <th scope="col">الملاحظات</th>
        </tr>
      </thead>
      <tbody>
        ${cards
          .map((card) => `
            <tr>
              <td>${escapeHtml(card.question)}</td>
              <td>${escapeHtml(card.answer)}</td>
              <td>${escapeHtml(card.notes || "")}</td>
            </tr>
          `)
          .join("")}
      </tbody>
    </table>
  `;
}

async function renderReview(id) {
  setActiveNav("categories");
  const payload = await api(`/api/review/${id}`);
  state.review = {
    category: payload.category,
    queue: [...payload.cards],
    initialTotal: payload.cards.length || 1,
    session: payload.session || {},
    current: null,
    revealed: false,
    done: [],
    removed: 0,
    ratings: { easy: 0, hard: 0, wrong: 0 },
    graduated: 0,
    startedAt: new Date(),
  };
  advanceReviewCard();
}

function advanceReviewCard(focusTarget = "question") {
  const review = state.review;
  review.revealed = false;
  review.current = review.queue.shift() || null;
  renderReviewSession(focusTarget);
}

function reviewReturnUrl() {
  const category = state.review?.category;
  if (!category) return "#/categories";
  return `#/categories/${category.id}`;
}

function renderReviewSession(focusTarget = null) {
  const review = state.review;
  if (!review) return;
  const card = review.current;
  const completed = review.done.length;
  const totalMoves = completed + review.queue.length + (card ? 1 : 0);

  if (!card && completed === 0) {
    view.innerHTML = `
      <section class="review-empty" aria-labelledby="review-empty-title">
        <p class="eyebrow">جلسة المراجعة</p>
        <h1 id="review-empty-title">لا توجد بطاقات مستحقة الآن</h1>
        <p>كل البطاقات في هذا النطاق مؤجلة لمواعيد لاحقة. النظام لا يريد أن يرهقك بلا داع.</p>
        <dl class="review-mini-stats">
          <div><dt>كل البطاقات</dt><dd>${review.session.total_cards || 0}</dd></div>
          <div><dt>مرحلة التعلم</dt><dd>${review.session.learning_cards || 0}</dd></div>
          <div><dt>مرحلة المراجعة</dt><dd>${review.session.review_cards || 0}</dd></div>
        </dl>
        <a class="button-link primary" href="#/categories">العودة إلى الصفحة الرئيسية</a>
      </section>
    `;
    return;
  }

  if (!card) {
    renderReviewSummary(false);
    return;
  }

  view.innerHTML = `
    <section class="review-stage" aria-labelledby="review-title">
      <header class="review-topbar">
        <div>
          <p class="eyebrow">جلسة مراجعة</p>
          <h1 id="review-title">${escapeHtml(review.category.name)}</h1>
          <p class="subtle">تمت مراجعة ${completed}، متبقّي في هذه الجلسة ${review.queue.length + 1}، وإجمالي الحركة الحالية ${totalMoves} بطاقة.</p>
        </div>
        <div class="toolbar">
          <button id="finish-review">إنهاء المراجعة الآن</button>
          <a class="button-link" href="${reviewReturnUrl()}">الخروج</a>
        </div>
      </header>

      <div class="review-progress" aria-label="تقدم الجلسة">
        <span style="width: ${Math.min(100, Math.round((completed / Math.max(totalMoves, 1)) * 100))}%"></span>
      </div>

      <article class="study-card">
        <div class="card-kicker">
          <span>${escapeHtml(card.category_name || "بطاقة")}</span>
          <span>${card.stats.stage === "learning" ? "مرحلة التعلم" : "مراجعة مجدولة"}</span>
        </div>
        <section class="question-block" aria-labelledby="question-title">
          <h2 id="question-title">السؤال</h2>
          <p id="question-body" class="review-focus-body" tabindex="-1">${escapeHtml(card.question)}</p>
        </section>
        ${review.revealed ? `
          <section class="answer-block" aria-label="الإجابة">
            <p class="block-label">الإجابة</p>
            <p id="answer-body" class="review-focus-body" tabindex="-1">${escapeHtml(card.answer)}</p>
          </section>
        ` : ""}
        ${review.revealed && card.notes ? `
          <section class="notes-block" aria-labelledby="notes-title">
            <h2 id="notes-title">الملاحظات</h2>
            <p>${escapeHtml(card.notes)}</p>
          </section>
        ` : ""}
      </article>

      <div class="review-actions" aria-label="إجراءات البطاقة">
        ${review.revealed ? `
          <button class="rating easy" data-rate="easy">سهل</button>
          <button class="rating hard" data-rate="hard">صعب</button>
          <button class="rating wrong" data-rate="wrong">خطأ</button>
        ` : `<button class="primary" id="show-answer">عرض الإجابة</button>`}
        <button class="danger" id="destroy-card">إعدام البطاقة</button>
      </div>

      <details class="review-details">
        <summary>إحصائيات هذه البطاقة</summary>
        <dl class="card-stat-grid">
          <div><dt>عدد المراجعات</dt><dd>${card.stats.review_count}</dd></div>
          <div><dt>سهل</dt><dd>${card.stats.easy_count}</dd></div>
          <div><dt>صعب</dt><dd>${card.stats.hard_count}</dd></div>
          <div><dt>خطأ</dt><dd>${card.stats.wrong_count}</dd></div>
          <div><dt>المطلوب للتخرج</dt><dd>${card.stats.remaining_easy} سهل متتالي</dd></div>
          <div><dt>الدقة</dt><dd>${card.stats.accuracy_percent === null ? "لا توجد بعد" : `${card.stats.accuracy_percent}%`}</dd></div>
          <div><dt>موعدها الحالي</dt><dd>${formatDateTime(card.stats.due_at)}</dd></div>
          <div><dt>آخر مراجعة</dt><dd>${card.stats.last_reviewed_at ? formatDateTime(card.stats.last_reviewed_at) : "لم تراجع بعد"}</dd></div>
        </dl>
      </details>
    </section>
  `;

  const showAnswer = document.querySelector("#show-answer");
  if (showAnswer) {
    showAnswer.addEventListener("click", () => {
      review.revealed = true;
      renderReviewSession("answer");
    });
  }

  document.querySelectorAll("[data-rate]").forEach((button) => {
    button.addEventListener("click", () => rateCurrentCard(button.dataset.rate));
  });

  document.querySelector("#destroy-card").addEventListener("click", destroyCurrentCard);
  document.querySelector("#finish-review").addEventListener("click", () => renderReviewSummary(true));
  focusReviewBody(focusTarget);
}

function focusReviewBody(target) {
  if (!target) return;
  const selector = target === "answer" ? "#answer-body" : "#question-body";
  const focusElement = () => {
    const element = document.querySelector(selector);
    if (!element) return;
    element.focus();
    element.scrollIntoView({ block: "center", inline: "nearest" });
  };
  focusElement();
  requestAnimationFrame(focusElement);
  setTimeout(focusElement, 60);
}

async function rateCurrentCard(rating) {
  const review = state.review;
  if (!review?.current) return;
  try {
    const result = await api(`/api/review/cards/${review.current.id}/answer`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
    review.ratings[rating] += 1;
    review.graduated += result.graduated ? 1 : 0;
    review.done.push({ ...review.current, rating, next_due_at: result.next_due_at });
    if (result.requeue_after_ratio !== null && result.requeue_after_ratio !== undefined) {
      const offset = Math.max(1, Math.ceil(review.initialTotal * result.requeue_after_ratio));
      review.queue.splice(Math.min(offset - 1, review.queue.length), 0, result.card);
    }
    announce("تم تسجيل التقييم");
    advanceReviewCard("question");
  } catch (error) {
    showError(error);
  }
}

async function destroyCurrentCard() {
  const review = state.review;
  if (!review?.current) return;
  if (!confirm("سيتم حذف هذه البطاقة نهائيا. هل تريد المتابعة؟")) return;
  try {
    await api(`/api/cards/${review.current.id}`, { method: "DELETE" });
    review.removed += 1;
    review.done.push({ ...review.current, rating: "removed" });
    announce("تم حذف البطاقة");
    advanceReviewCard("question");
  } catch (error) {
    showError(error);
  }
}

function renderReviewSummary(stoppedEarly) {
  const review = state.review;
  const reviewed = review.done.filter((item) => item.rating !== "removed").length;
  view.innerHTML = `
    <section class="review-summary" aria-labelledby="summary-title">
      <p class="eyebrow">${stoppedEarly ? "تم إيقاف الجلسة" : "انتهت المراجعة"}</p>
      <h1 id="summary-title">${stoppedEarly ? "حفظنا تقدمك لهذه الجلسة" : "أحسنت، خلصت جلسة المراجعة"}</h1>
      <p class="subtle">البطاقات التي لم تراجعها ستظل في موعدها الحالي، وتقدر ترجع لها لاحقا من نفس زر بدء المراجعة.</p>
      <dl class="summary-grid">
        <div><dt>تمت مراجعتها</dt><dd>${reviewed}</dd></div>
        <div><dt>سهل</dt><dd>${review.ratings.easy}</dd></div>
        <div><dt>صعب</dt><dd>${review.ratings.hard}</dd></div>
        <div><dt>خطأ</dt><dd>${review.ratings.wrong}</dd></div>
        <div><dt>تخرجت</dt><dd>${review.graduated}</dd></div>
        <div><dt>تم حذفها</dt><dd>${review.removed}</dd></div>
        <div><dt>متبقية دون مراجعة</dt><dd>${review.queue.length + (review.current ? 1 : 0)}</dd></div>
      </dl>
      <div class="toolbar">
        <a class="button-link primary" href="#/categories">العودة إلى الصفحة الرئيسية</a>
        <a class="button-link" href="${reviewReturnUrl()}">العودة للقسم</a>
      </div>
    </section>
  `;
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ar", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

async function renderSettings() {
  setActiveNav("settings");
  state.settings = await api("/api/settings");
  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>الإعدادات</h1>
        <p class="subtle">مفاتيح Gemini والهوية والنموذج الافتراضي.</p>
      </div>
    </div>
    <div class="settings-grid">
      <section class="panel" aria-labelledby="profile-title">
        <h2 id="profile-title">البيانات العامة</h2>
        <form id="settings-form" class="field-stack">
          <div class="field">
            <label for="user-name">اسمك</label>
            <input id="user-name" value="${escapeHtml(state.settings.user_name || "")}" />
          </div>
          <div class="field">
            <label for="main-prompt">البرومبت الرئيسي</label>
            <textarea id="main-prompt">${escapeHtml(state.settings.main_prompt || "")}</textarea>
          </div>
          <div class="field">
            <label for="companion-context">سياق رفيق الرفقاء القابل للتعديل</label>
            <textarea id="companion-context">${escapeHtml(state.settings.companion_context || "")}</textarea>
          </div>
          <div class="field">
            <label for="default-model">النموذج الافتراضي</label>
            <select id="default-model"></select>
          </div>
          <div class="form-actions">
            <button class="primary" type="submit">حفظ الإعدادات</button>
            <button type="button" id="fetch-models">بحث عن نماذج</button>
          </div>
        </form>
      </section>
      <section class="panel" aria-labelledby="keys-title">
        <h2 id="keys-title">مفاتيح API</h2>
        <form id="key-form" class="field-stack">
          <div class="field">
            <label for="key-label">اسم المفتاح</label>
            <input id="key-label" autocomplete="off" />
          </div>
          <div class="field">
            <label for="key-value">المفتاح</label>
            <input id="key-value" autocomplete="off" />
          </div>
          <button class="primary" type="submit">إضافة مفتاح</button>
        </form>
        <div id="key-list" class="key-list"></div>
      </section>
    </div>
  `;
  populateModelSelect();
  renderKeys();

  document.querySelector("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      user_name: document.querySelector("#user-name").value,
      main_prompt: document.querySelector("#main-prompt").value,
      companion_context: document.querySelector("#companion-context").value,
      default_model: document.querySelector("#default-model").value,
    };
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    announce("تم حفظ الإعدادات");
  });

  document.querySelector("#fetch-models").addEventListener("click", async () => {
    announce("جار جلب النماذج");
    const payload = await api("/api/settings/models/search", { method: "POST", body: JSON.stringify({}) });
    state.models = payload.models;
    populateModelSelect();
    announce("تم جلب النماذج");
  });

  document.querySelector("#key-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const key_value = document.querySelector("#key-value").value;
    const label = document.querySelector("#key-label").value;
    state.settings = await api("/api/settings/api-keys", { method: "POST", body: JSON.stringify({ key_value, label }) });
    document.querySelector("#key-value").value = "";
    document.querySelector("#key-label").value = "";
    renderKeys();
    announce("تمت إضافة المفتاح");
  });
}

function populateModelSelect() {
  const select = document.querySelector("#default-model");
  if (!select) return;
  const saved = state.settings?.default_model || "";
  const options = [...state.models];
  if (saved && !options.some((model) => model.name === saved)) {
    options.unshift({ name: saved, display_name: saved.replace("models/", "") });
  }
  if (!options.length) {
    options.push({ name: saved || "models/gemini-1.5-flash", display_name: saved || "gemini-1.5-flash" });
  }
  select.innerHTML = options
    .map((model) => `<option value="${escapeHtml(model.name)}">${escapeHtml(model.display_name)}</option>`)
    .join("");
  select.value = saved || options[0].name;
}

function renderKeys() {
  const container = document.querySelector("#key-list");
  if (!container) return;
  const keys = state.settings?.api_keys || [];
  if (!keys.length) {
    container.innerHTML = `<div class="empty-state">لا توجد مفاتيح محفوظة.</div>`;
    return;
  }
  container.innerHTML = keys
    .map((key) => `
      <div class="key-row">
        <div>
          <strong>${escapeHtml(key.label)}</strong>
          <div class="subtle">${escapeHtml(key.masked)}</div>
        </div>
        <button class="compact danger" data-delete-key="${key.id}">حذف</button>
      </div>
    `)
    .join("");
  container.querySelectorAll("[data-delete-key]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/settings/api-keys/${button.dataset.deleteKey}`, { method: "DELETE" });
      state.settings = await api("/api/settings");
      renderKeys();
      announce("تم حذف المفتاح");
    });
  });
}

async function renderCompanion() {
  setActiveNav("companion");
  state.settings = await api("/api/settings");
  if (!state.chatModel) state.chatModel = state.settings.default_model || "models/gemini-1.5-flash";
  view.innerHTML = `
    <section class="chat-layout" aria-labelledby="chat-title">
      <div class="chat-head">
        <div>
          <h1 id="chat-title">رفيق الرفقاء</h1>
          <p class="subtle">جلسة مؤقتة لا تحفظ في قاعدة البيانات.</p>
        </div>
        <div class="field chat-model">
          <label for="session-model">نموذج الجلسة</label>
          <select id="session-model"></select>
        </div>
      </div>
      <div id="messages" class="messages" aria-live="off" aria-label="المحادثة"></div>
      <form id="chat-form" class="chat-form">
        <div class="field">
          <label for="chat-input">رسالتك</label>
          <textarea id="chat-input" required></textarea>
        </div>
        <button class="primary" type="submit">إرسال</button>
      </form>
    </section>
  `;
  populateSessionModel();
  renderMessages();
  document.querySelector("#session-model").addEventListener("change", (event) => {
    state.chatModel = event.target.value;
  });
  document.querySelector("#chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.querySelector("#chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    state.chat.push({ role: "user", text });
    await requestAssistant();
  });
}

function populateSessionModel() {
  const select = document.querySelector("#session-model");
  if (!select) return;
  const models = [...state.models];
  if (state.chatModel && !models.some((model) => model.name === state.chatModel)) {
    models.unshift({ name: state.chatModel, display_name: state.chatModel.replace("models/", "") });
  }
  select.innerHTML = models
    .map((model) => `<option value="${escapeHtml(model.name)}">${escapeHtml(model.display_name)}</option>`)
    .join("");
  if (!select.innerHTML) {
    select.innerHTML = `<option value="${escapeHtml(state.chatModel)}">${escapeHtml(state.chatModel.replace("models/", ""))}</option>`;
  }
  select.value = state.chatModel;
}

function renderMessages() {
  const container = document.querySelector("#messages");
  if (!container) return;
  container.innerHTML = state.chat
    .map((message, index) => {
      return `
        <article class="message ${message.role}" data-message-index="${index}">
          ${message.role === "model" ? `<h3 class="message-name">رفيق الرفقاء</h3>` : ""}
          <div class="bubble" dir="auto" data-bubble-index="${index}">${message.role === "model" ? markdownToHtml(cleanAssistantText(message.text) || "جار الكتابة") : escapeHtml(message.text)}</div>
          <div data-traces-index="${index}">${renderTraceHtml(message)}</div>
          ${message.role === "user" ? `
            <div class="message-actions">
              <button class="compact" data-regenerate="${index}">إعادة توليد الإجابة</button>
            </div>
          ` : ""}
        </article>
      `;
    })
    .join("");
  container.querySelectorAll("[data-regenerate]").forEach((button) => {
    button.addEventListener("click", async () => {
      const index = Number(button.dataset.regenerate);
      state.chat = state.chat.slice(0, index + 1);
      await requestAssistant();
    });
  });
  container.scrollTop = container.scrollHeight;
}

function renderTraceHtml(message) {
  return [
    ...(message.roadmap ? [`<details class="trace roadmap-trace"><summary>خارطة العمل</summary><div class="trace-body">${markdownToHtml(message.roadmap)}</div></details>`] : []),
    ...(message.thinking ? [`<details class="trace"><summary>التفكير</summary><pre>${escapeHtml(message.thinking)}</pre></details>`] : []),
    ...((message.tools || []).map((tool) => `
      <details class="trace">
        <summary>أداة: ${escapeHtml(tool.name)}</summary>
        <pre>${escapeHtml(JSON.stringify({ args: tool.args, result: tool.result }, null, 2))}</pre>
      </details>
    `)),
  ].join("");
}

function updateMessageElement(index) {
  const message = state.chat[index];
  const bubble = document.querySelector(`[data-bubble-index="${index}"]`);
  const traces = document.querySelector(`[data-traces-index="${index}"]`);
  const container = document.querySelector("#messages");
  if (bubble) {
    bubble.innerHTML = message.role === "model" ? markdownToHtml(cleanAssistantText(message.text) || "جار الكتابة") : escapeHtml(message.text);
  }
  if (traces) traces.innerHTML = renderTraceHtml(message);
  if (container) container.scrollTop = container.scrollHeight;
}

function markdownToHtml(text = "") {
  const lines = escapeHtml(text).split("\n");
  const html = [];
  let inCode = false;
  let inList = false;
  let inOrderedList = false;

  const closeLists = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
    if (inOrderedList) {
      html.push("</ol>");
      inOrderedList = false;
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      closeLists();
      html.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      html.push(`${line}\n`);
      continue;
    }
    if (/^#{1,3}\s+/.test(line)) {
      closeLists();
      const level = line.match(/^#+/)[0].length;
      html.push(`<h${level + 2}>${formatInline(line.replace(/^#{1,3}\s+/, ""))}</h${level + 2}>`);
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inList) {
        closeLists();
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${formatInline(line.replace(/^\s*[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      if (!inOrderedList) {
        closeLists();
        html.push("<ol>");
        inOrderedList = true;
      }
      html.push(`<li>${formatInline(line.replace(/^\s*\d+\.\s+/, ""))}</li>`);
      continue;
    }
    closeLists();
    html.push(line.trim() ? `<p>${formatInline(line)}</p>` : "");
  }
  closeLists();
  if (inCode) html.push("</code></pre>");
  return html.join("");
}

function cleanAssistantText(text = "") {
  const paragraphs = String(text).split(/\n\s*\n/);
  const cleaned = [];
  let skippingInternalList = false;
  for (const paragraph of paragraphs) {
    if (isInternalAssistantParagraph(paragraph)) {
      skippingInternalList = true;
      continue;
    }
    if (skippingInternalList && isLikelyInternalList(paragraph)) {
      continue;
    }
    skippingInternalList = false;
    cleaned.push(paragraph);
  }
  return cleaned.join("\n\n").trim();
}

function isLikelyInternalList(paragraph) {
  const text = paragraph.trim().toLowerCase();
  if (!text) return false;
  return (
    /^[-*•]\s+/.test(text) ||
    /^\d+\.\s+/.test(text) ||
    /^this suggests\b/.test(text) ||
    /^therefore\b/.test(text) ||
    /^so\b/.test(text)
  );
}

function isInternalAssistantParagraph(paragraph) {
  const text = paragraph.trim();
  if (!text) return false;
  const normalized = text.toLowerCase();
  const internalPatterns = [
    /^the user is asking\b/,
    /^i have already\b/,
    /^i already\b/,
    /^i need to\b/,
    /^i should\b/,
    /^i will\b/,
    /^now i\b/,
    /^this suggests\b/,
    /^based on (the )?(tool|function|database|statistics)/,
    /^the tool\b/,
    /^tool result\b/,
    /^function call\b/,
    /^called\s+[`"]?[a-z_]+/,
    /get_platform_statistics/,
    /query_review_database/,
    /get_database_schema/,
    /functionresponse/,
    /functioncall/,
    /^المستخدم يسأل/,
    /^لقد استدعيت/,
    /^استدعيت أداة/,
    /^سأستخدم الأداة/,
    /^بناء على نتيجة الأداة/,
    /^نتيجة الأداة/,
  ];
  return internalPatterns.some((pattern) => pattern.test(normalized));
}

function formatInline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

async function requestAssistant() {
  state.chat.push({ role: "model", text: "", thinking: "", roadmap: "", tools: [] });
  renderMessages();
  announce("رفيق الرفقاء يكتب الرد");
  const modelIndex = state.chat.length - 1;
  const modelMessage = state.chat[state.chat.length - 1];
  const messages = state.chat
    .slice(0, -1)
    .slice(-15)
    .filter((message) => message.role === "user" || message.role === "model")
    .map((message) => ({ role: message.role, text: cleanAssistantText(message.text || "") }));
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, model: state.chatModel }),
    });
    if (!response.ok || !response.body) throw new Error("تعذر بدء البث");
    await readEventStream(response, (event, payload) => {
      if (event === "delta") modelMessage.text += payload.text || "";
      if (event === "roadmap_delta") modelMessage.roadmap += payload.text || "";
      if (event === "thinking" || event === "thinking_delta") modelMessage.thinking += `${payload.text || ""}\n`;
      if (event === "tool_call") modelMessage.tools.push(payload);
      if (event === "error") modelMessage.text += `\n${payload.message}`;
      updateMessageElement(modelIndex);
    });
    announce("انتهى رفيق الرفقاء من الرد");
  } catch (error) {
    modelMessage.text = error.message;
    updateMessageElement(modelIndex);
    showError(error);
  }
}

window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  showError(event.reason);
});

async function readEventStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const event = block.match(/^event:\s*(.+)$/m)?.[1] || "message";
      const data = block.match(/^data:\s*(.+)$/m)?.[1] || "{}";
      onEvent(event, JSON.parse(data));
    }
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
