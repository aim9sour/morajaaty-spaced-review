const state = {
  currentCategory: null,
  settings: null,
  models: [],
  chat: [],
  chatModel: "",
  chatProviderId: "",
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
const dialogConceptField = document.querySelector("#dialog-concept-field");
const dialogConcept = document.querySelector("#dialog-concept");
let reviewSpeechClearTimer = null;

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

function speakReviewText(text) {
  if (!liveRegion) return;
  if (reviewSpeechClearTimer) clearTimeout(reviewSpeechClearTimer);
  liveRegion.textContent = "";
  requestAnimationFrame(() => {
    liveRegion.textContent = text || "";
    if (text) {
      reviewSpeechClearTimer = setTimeout(() => {
        if (liveRegion.textContent === text) liveRegion.textContent = "";
      }, 1500);
    }
  });
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

function openNameDialog(title, initialValue = "", options = {}) {
  return new Promise((resolve) => {
    dialogTitle.textContent = title;
    dialogName.value = initialValue;
    dialogConcept.checked = Boolean(options.isConceptRoot);
    dialogConceptField.hidden = !options.withConceptCheckbox;
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
      if (options.withConceptCheckbox) {
        resolve({ name: value, is_concept_root: dialogConcept.checked });
      } else {
        resolve(value);
      }
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
    const details = await openNameDialog("إضافة قسم جديد", "", { withConceptCheckbox: true });
    if (!details) return;
    await api("/api/categories", { method: "POST", body: JSON.stringify(details) });
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
      const badge = category.is_concept_mode ? `<span class="badge">مفاهيم برمجية</span>` : "";
      return `
        <article class="item-card">
          <header>
            <div class="item-main">
              <a class="item-title" href="#/categories/${category.id}">${escapeHtml(category.name)}</a>
              <span class="subtle">${meta}</span>
              ${badge}
            </div>
            <div class="row-actions" aria-label="إجراءات ${escapeHtml(category.name)}">
              <a class="button-link" href="#/review/${category.id}">بدء المراجعة</a>
              <button class="compact" data-action="rename" data-id="${category.id}" data-name="${escapeHtml(category.name)}" data-parent-id="${category.parent_id ?? ""}" data-concept-root="${category.is_concept_root ? "1" : "0"}">إعادة تسمية</button>
              <button class="compact danger" data-action="delete" data-id="${category.id}">حذف</button>
            </div>
          </header>
        </article>
      `;
    })
    .join("");

  container.querySelectorAll("[data-action='rename']").forEach((button) => {
    button.addEventListener("click", async () => {
      const isRoot = button.dataset.parentId === "";
      const details = await openNameDialog("تعديل القسم", button.dataset.name, {
        withConceptCheckbox: isRoot,
        isConceptRoot: button.dataset.conceptRoot === "1",
      });
      if (!details) return;
      const payload = isRoot ? details : { name: details };
      await api(`/api/categories/${button.dataset.id}`, { method: "PATCH", body: JSON.stringify(payload) });
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
  const conceptMode = Boolean(category.is_concept_mode);
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
        <p class="subtle">${cards.length} ${conceptMode ? "مفهوم" : "بطاقة"}</p>
      </div>
      <a class="button-link primary" href="#/review/${category.id}">بدء المراجعة</a>
    </div>
    <section class="panel upload-box" aria-labelledby="upload-title">
      <h2 id="upload-title">${conceptMode ? "رفع مفاهيم" : "رفع بطاقات JSON"}</h2>
      <p class="subtle">${conceptMode ? "في قسم المفاهيم ارفع ملف JSON أو ملف نصي، ويمكن كتابة كل مفهوم داخل [] في سطر مستقل." : "ارفع بطاقات question/answer أو front/back."}</p>
      <form id="upload-form" class="field-stack">
        <div class="field">
          <label for="cards-file">ملف البطاقات</label>
          <input id="cards-file" name="file" type="file" accept="${conceptMode ? ".json,.txt,text/plain,application/json" : "application/json,.json"}" required />
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
  renderCards(cards, conceptMode);
}

function renderCards(cards, conceptMode = false) {
  const container = document.querySelector("#cards-list");
  if (!cards.length) {
    container.innerHTML = `<div class="empty-state">لا توجد ${conceptMode ? "مفاهيم" : "بطاقات"} في هذا القسم.</div>`;
    return;
  }
  if (conceptMode) {
    container.innerHTML = `
      <table class="cards-table">
        <thead>
          <tr>
            <th scope="col">المفهوم</th>
            <th scope="col">مرحلة المفهوم</th>
            <th scope="col">أيام تثبيت متبقية</th>
          </tr>
        </thead>
        <tbody>
          ${cards
            .map((card) => `
              <tr>
                <td>${escapeHtml(card.question)}</td>
                <td>${card.stage === "review" ? "تكرار بعيد" : "تثبيت يومي"}</td>
                <td>${card.concept_debt || 0}</td>
              </tr>
            `)
            .join("")}
        </tbody>
      </table>
    `;
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
              <td>${escapeHtml(card.notes || "")}${(card.variant_count || 1) > 1 ? ` (${card.variant_count} نسخ)` : ""}</td>
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
    conceptMode: Boolean(payload.concept_mode),
    currentIndex: 0,
    current: null,
    revealed: false,
    done: [],
    removed: 0,
    ratings: { easy: 0, hard: 0, wrong: 0 },
    graduated: 0,
    regraduated: 0,
    ratingPending: false,
    startedAt: new Date(),
  };
  advanceReviewCard();
}

function advanceReviewCard(focusTarget = "question") {
  const review = state.review;
  if (review.conceptMode) {
    review.revealed = true;
    review.current = review.queue[review.currentIndex] || null;
    renderReviewSession(focusTarget);
    return;
  }
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

  if (review.conceptMode) {
    renderConceptReviewSession(focusTarget);
    return;
  }

  renderNormalReviewShell();
  updateNormalReviewCardView(focusTarget, card, completed, totalMoves);
}

function renderNormalReviewShell() {
  if (document.querySelector("#normal-review-stage")) return;
  view.innerHTML = `
    <section id="normal-review-stage" class="review-stage">
      <header class="review-topbar">
        <div>
          <p class="eyebrow">جلسة مراجعة</p>
          <h1 id="review-title"></h1>
          <p id="review-session-line" class="subtle"></p>
        </div>
        <div class="toolbar">
          <button id="finish-review">إنهاء المراجعة الآن</button>
          <a id="review-exit-link" class="button-link" href="#/categories">الخروج</a>
        </div>
      </header>

      <div class="review-progress" aria-label="تقدم الجلسة">
        <span id="review-progress-bar"></span>
      </div>

      <article class="study-card">
        <div class="card-kicker">
          <span id="card-category-name"></span>
          <span id="card-stage-label"></span>
        </div>
        <section class="question-block" aria-labelledby="question-title">
          <h2 id="question-title">السؤال</h2>
          <p id="question-body" class="review-focus-body" tabindex="-1"></p>
        </section>
        <section id="answer-section" class="answer-block" aria-label="الإجابة" hidden>
          <p id="answer-label" class="block-label review-focus-body" tabindex="-1">الإجابة</p>
          <p id="answer-body" class="review-focus-body" tabindex="-1"></p>
        </section>
        <section id="notes-section" class="notes-block" hidden>
          <h2 id="notes-title">الملاحظات</h2>
          <p id="notes-body" class="review-focus-body" tabindex="-1"></p>
        </section>
      </article>

      <div class="review-actions" aria-label="إجراءات البطاقة">
        <button class="primary" id="show-answer">عرض الإجابة</button>
        <button id="rating-easy" class="rating easy" data-rate="easy" hidden disabled>سهل</button>
        <button id="rating-hard" class="rating hard" data-rate="hard" hidden disabled>صعب</button>
        <button id="rating-wrong" class="rating wrong" data-rate="wrong" hidden disabled>خطأ</button>
        <button class="danger" id="destroy-card">إعدام البطاقة</button>
      </div>

      <details class="review-details">
        <summary>إحصائيات هذه البطاقة</summary>
        <dl class="card-stat-grid">
          <div><dt>عدد المراجعات</dt><dd id="stat-review-count"></dd></div>
          <div><dt>سهل</dt><dd id="stat-easy-count"></dd></div>
          <div><dt>صعب</dt><dd id="stat-hard-count"></dd></div>
          <div><dt>خطأ</dt><dd id="stat-wrong-count"></dd></div>
          <div><dt>مرات دخول المراجعة</dt><dd id="stat-graduated-count"></dd></div>
          <div><dt>المطلوب للتخرج</dt><dd id="stat-remaining-easy"></dd></div>
          <div><dt>الدقة</dt><dd id="stat-accuracy"></dd></div>
          <div><dt>موعدها الحالي</dt><dd id="stat-due-at"></dd></div>
          <div><dt>آخر مراجعة</dt><dd id="stat-last-reviewed"></dd></div>
        </dl>
      </details>
    </section>
  `;

  document.querySelector("#show-answer").addEventListener("click", () => {
    const review = state.review;
    if (!review?.current) return;
    review.revealed = true;
    renderReviewSession("answer");
  });
  document.querySelectorAll("[data-rate]").forEach((button) => {
    button.addEventListener("click", () => rateCurrentCard(button.dataset.rate));
  });
  document.querySelector("#destroy-card").addEventListener("click", destroyCurrentCard);
  document.querySelector("#finish-review").addEventListener("click", () => renderReviewSummary(true));
}

function updateNormalReviewCardView(focusTarget, card, completed, totalMoves) {
  const review = state.review;
  document.querySelector("#review-title").textContent = review.category.name;
  document.querySelector("#review-session-line").textContent =
    `تمت مراجعة ${completed}، متبقّي في هذه الجلسة ${review.queue.length + 1}، وإجمالي الحركة الحالية ${totalMoves} بطاقة.`;
  document.querySelector("#review-exit-link").setAttribute("href", reviewReturnUrl());
  document.querySelector("#review-progress-bar").style.width =
    `${Math.min(100, Math.round((completed / Math.max(totalMoves, 1)) * 100))}%`;
  document.querySelector("#card-category-name").textContent = card.category_name || "بطاقة";
  document.querySelector("#card-stage-label").textContent =
    card.stats.stage === "learning" ? "مرحلة التعلم" : "مراجعة مجدولة";
  document.querySelector("#question-body").textContent = card.question;

  const showAnswer = Boolean(review.revealed);
  document.querySelector("#answer-section").hidden = !showAnswer;
  document.querySelector("#answer-body").textContent = showAnswer ? card.answer : "";
  const showNotes = Boolean(showAnswer && card.notes);
  document.querySelector("#notes-section").hidden = !showNotes;
  document.querySelector("#notes-body").textContent = showNotes ? card.notes : "";

  document.querySelector("#stat-review-count").textContent = card.stats.review_count;
  document.querySelector("#stat-easy-count").textContent = card.stats.easy_count;
  document.querySelector("#stat-hard-count").textContent = card.stats.hard_count;
  document.querySelector("#stat-wrong-count").textContent = card.stats.wrong_count;
  document.querySelector("#stat-graduated-count").textContent = card.stats.graduated_count;
  document.querySelector("#stat-remaining-easy").textContent = `${card.stats.remaining_easy} سهل متتالي`;
  document.querySelector("#stat-accuracy").textContent =
    card.stats.accuracy_percent === null ? "لا توجد بعد" : `${card.stats.accuracy_percent}%`;
  document.querySelector("#stat-due-at").textContent = formatDateOnly(card.stats.due_at);
  document.querySelector("#stat-last-reviewed").textContent =
    card.stats.last_reviewed_at ? formatDateTime(card.stats.last_reviewed_at) : "لم تراجع بعد";

  setReviewMode(review.revealed ? "answer" : "question");
  handleReviewFocus(focusTarget, card);
}

function setReviewMode(mode) {
  const showingQuestion = mode === "question";
  const ratingPending = Boolean(state.review?.ratingPending);
  const showAnswer = document.querySelector("#show-answer");
  showAnswer.hidden = !showingQuestion;
  showAnswer.disabled = !showingQuestion;
  document.querySelectorAll("[data-rate]").forEach((button) => {
    button.hidden = showingQuestion;
    button.disabled = showingQuestion || ratingPending;
  });
}

function renderConceptReviewSession(focusTarget = null) {
  const review = state.review;
  const card = review.current;
  const completed = review.done.length;
  const pending = review.queue.length;
  const currentNumber = pending ? review.currentIndex + 1 : 0;
  view.innerHTML = `
    <section class="review-stage">
      <header class="review-topbar">
        <div>
          <p class="eyebrow">جلسة مفاهيم برمجية</p>
          <h1 id="review-title">${escapeHtml(review.category.name)}</h1>
          <p class="subtle">تم تقييم ${completed}، متبقّي دون تقييم ${pending}، والمفهوم الحالي ${currentNumber} من ${Math.max(pending, 1)}.</p>
        </div>
        <div class="toolbar">
          <button id="finish-review">إنهاء المراجعة الآن</button>
          <a class="button-link" href="${reviewReturnUrl()}">الخروج</a>
        </div>
      </header>

      <div class="review-progress" aria-label="تقدم الجلسة">
        <span style="width: ${Math.min(100, Math.round((completed / Math.max(review.initialTotal, 1)) * 100))}%"></span>
      </div>

      <article class="study-card concept-card">
        <div class="card-kicker">
          <span>${escapeHtml(card.category_name || "مفهوم")}</span>
          <span>${card.stage === "review" ? "تكرار بعيد" : "تثبيت يومي"}</span>
          ${card.concept_debt ? `<span>${card.concept_debt} يوم تثبيت متبقّي</span>` : ""}
        </div>
        <section class="question-block" aria-labelledby="concept-title">
          <h2 id="concept-title">المفهوم</h2>
          <p id="question-body" class="review-focus-body concept-body" tabindex="-1">${escapeHtml(card.question)}</p>
        </section>
      </article>

      <div class="review-actions" aria-label="تصفح وتقييم المفهوم">
        <button id="prev-concept" type="button" ${review.currentIndex <= 0 ? "disabled" : ""}>السابق</button>
        <button id="next-concept" type="button" ${review.currentIndex >= pending - 1 ? "disabled" : ""}>التالي</button>
        <button class="rating easy" data-rate="easy">سهل</button>
        <button class="rating hard" data-rate="hard">صعب</button>
        <button class="rating wrong" data-rate="wrong">خطأ</button>
        <button class="danger" id="destroy-card">إعدام المفهوم</button>
      </div>

      <div class="field concept-picker">
        <label for="concept-jump">انتقال مباشر لمفهوم داخل الجلسة</label>
        <select id="concept-jump">
          ${review.queue.map((item, index) => `<option value="${index}" ${index === review.currentIndex ? "selected" : ""}>${index + 1}. ${escapeHtml(item.question.slice(0, 90))}</option>`).join("")}
        </select>
      </div>

      <details class="review-details">
        <summary>إحصائيات هذا المفهوم</summary>
        <dl class="card-stat-grid">
          <div><dt>عدد المراجعات</dt><dd>${card.stats.review_count}</dd></div>
          <div><dt>سهل</dt><dd>${card.stats.easy_count}</dd></div>
          <div><dt>صعب</dt><dd>${card.stats.hard_count}</dd></div>
          <div><dt>خطأ</dt><dd>${card.stats.wrong_count}</dd></div>
          <div><dt>مرحلة التكرار</dt><dd>${card.stage === "review" ? (card.interval_index + 1) : "تثبيت"}</dd></div>
          <div><dt>أيام تثبيت متبقية</dt><dd>${card.concept_debt || 0}</dd></div>
          <div><dt>موعده الحالي</dt><dd>${formatDateOnly(card.stats.due_at)}</dd></div>
        </dl>
      </details>
    </section>
  `;

  document.querySelector("#prev-concept").addEventListener("click", () => moveConcept(-1));
  document.querySelector("#next-concept").addEventListener("click", () => moveConcept(1));
  document.querySelector("#concept-jump").addEventListener("change", (event) => {
    review.currentIndex = Number(event.target.value);
    advanceReviewCard("question");
  });
  document.querySelectorAll("[data-rate]").forEach((button) => {
    button.addEventListener("click", () => rateCurrentCard(button.dataset.rate));
  });
  document.querySelector("#destroy-card").addEventListener("click", destroyCurrentCard);
  document.querySelector("#finish-review").addEventListener("click", () => renderReviewSummary(true));
  focusReviewBody(focusTarget);
}

function moveConcept(direction) {
  const review = state.review;
  review.currentIndex = Math.min(Math.max(review.currentIndex + direction, 0), review.queue.length - 1);
  advanceReviewCard("question");
}

function focusReviewControl(selector) {
  const element = document.querySelector(selector);
  if (!element) return;
  element.focus();
}

function handleReviewFocus(target, card) {
  if (!target || !card) return;
  if (target === "answer") {
    speakReviewText(card.answer);
    if (card.notes) {
      focusReviewControl("#notes-body");
      return;
    }
    focusReviewControl("#answer-label");
    return;
  }
  speakReviewText(card.question);
  focusReviewControl("#show-answer");
}

function focusReviewBody(target) {
  if (!target) return;
  const selector = target === "answer" ? "#answer-body" : "#question-body";
  const element = document.querySelector(selector);
  if (!element) return;
  element.focus();
  element.scrollIntoView({ block: "center", inline: "nearest" });
}

async function rateCurrentCard(rating) {
  const review = state.review;
  if (!review?.current || review.ratingPending) return;
  review.ratingPending = true;
  document.querySelectorAll("[data-rate]").forEach((button) => {
    button.disabled = true;
  });
  try {
    const result = await api(`/api/review/cards/${review.current.id}/answer`, {
      method: "POST",
      body: JSON.stringify({
        rating,
        variant_id: review.current.variant_id || null,
        review_count: review.current.stats?.review_count ?? review.current.review_count ?? 0,
      }),
    });
    review.ratings[rating] += 1;
    review.graduated += result.first_graduation ? 1 : 0;
    review.regraduated += result.regraduated ? 1 : 0;
    review.done.push({ ...review.current, rating, next_due_at: result.next_due_at });
    if (review.conceptMode) {
      review.queue.splice(review.currentIndex, 1);
      review.currentIndex = Math.min(review.currentIndex, Math.max(review.queue.length - 1, 0));
      announce("تم تقييم المفهوم وإزالته من جلسة اليوم");
      advanceReviewCard("question");
      return;
    }
    if (result.requeue_after_ratio !== null && result.requeue_after_ratio !== undefined) {
      const offset = Math.max(1, Math.ceil(review.initialTotal * result.requeue_after_ratio));
      review.queue.splice(Math.min(offset - 1, review.queue.length), 0, result.card);
    }
    advanceReviewCard("next-question");
  } catch (error) {
    showError(error);
  } finally {
    if (state.review === review) {
      review.ratingPending = false;
      if (review.conceptMode) {
        document.querySelectorAll("[data-rate]").forEach((button) => {
          button.disabled = false;
        });
      } else {
        setReviewMode(review.revealed ? "answer" : "question");
      }
    }
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
    if (review.conceptMode) {
      review.queue.splice(review.currentIndex, 1);
      review.currentIndex = Math.min(review.currentIndex, Math.max(review.queue.length - 1, 0));
      announce("تم حذف المفهوم");
      advanceReviewCard("question");
      return;
    }
    announce("تم حذف البطاقة");
    advanceReviewCard("question");
  } catch (error) {
    showError(error);
  }
}

function renderReviewSummary(stoppedEarly) {
  const review = state.review;
  const reviewed = review.done.filter((item) => item.rating !== "removed").length;
  const remaining = review.conceptMode ? review.queue.length : review.queue.length + (review.current ? 1 : 0);
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
        <div><dt>تخرجت لأول مرة</dt><dd>${review.graduated}</dd></div>
        <div><dt>رجعت للمراجعة بعد تعثر</dt><dd>${review.regraduated}</dd></div>
        <div><dt>تم حذفها</dt><dd>${review.removed}</dd></div>
        <div><dt>متبقية دون مراجعة</dt><dd>${remaining}</dd></div>
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

function formatDateOnly(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ar", {
    dateStyle: "medium",
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
            <select id="default-provider"></select>
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
        <h2 id="keys-title">إضافة مفتاح أو مزود AI</h2>
        <form id="key-form" class="field-stack">
          <div class="field">
            <label for="key-kind">نوع المفتاح</label>
            <select id="key-kind">
              <option value="gemini">Gemini API</option>
              <option value="openai">OpenAI-compatible</option>
            </select>
          </div>
          <div class="field">
            <label for="key-label">اسم المفتاح</label>
            <input id="key-label" autocomplete="off" />
          </div>
          <div class="field">
            <label for="key-value">API key</label>
            <input id="key-value" autocomplete="off" />
          </div>
          <div id="openai-provider-fields" class="field-stack" hidden>
            <div class="field"><label for="provider-base-url">Base URL</label><input id="provider-base-url" autocomplete="off" placeholder="https://api.example.com/v1" /></div>
            <div class="field"><label for="provider-organization">Organization</label><input id="provider-organization" autocomplete="off" /></div>
            <div class="field"><label for="provider-project">Project</label><input id="provider-project" autocomplete="off" /></div>
            <div class="field"><label for="provider-headers">Headers JSON</label><textarea id="provider-headers" placeholder='{"HTTP-Referer":"...","X-Title":"..."}'></textarea></div>
            <div class="field"><label for="provider-query">Query JSON</label><textarea id="provider-query" placeholder='{"api-version":"..."}'></textarea></div>
            <div class="field"><label for="provider-timeout">Timeout seconds</label><input id="provider-timeout" type="number" min="1" step="1" /></div>
            <div class="field"><label for="provider-retries">Max retries</label><input id="provider-retries" type="number" min="0" step="1" /></div>
          </div>
          <button class="primary" type="submit">إضافة</button>
        </form>
        <h3>مفاتيح Gemini المحفوظة</h3>
        <div id="key-list" class="key-list"></div>
      </section>
      <section class="panel" aria-labelledby="providers-title">
        <h2 id="providers-title">مزودات OpenAI-compatible</h2>
        <div id="provider-list" class="key-list"></div>
      </section>
    </div>
  `;
  const defaultProviderLabel = document.querySelector("#default-provider")?.previousElementSibling;
  if (defaultProviderLabel) {
    defaultProviderLabel.textContent = "المزود الافتراضي";
    defaultProviderLabel.setAttribute("for", "default-provider");
  }
  populateModelSelect();
  renderKeys();
  renderProviders();
  renderKeyKindFields();
  document.querySelector("#key-kind").addEventListener("change", renderKeyKindFields);

  document.querySelector("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const selectedProviderId = document.querySelector("#default-provider").value;
    const selectedModel = document.querySelector("#default-model").value;
    if (selectedProviderId && !selectedModel) {
      showError(new Error("اجلب نماذج المزود أولا ثم اختر نموذجا افتراضيا."));
      return;
    }
    const payload = {
      user_name: document.querySelector("#user-name").value,
      main_prompt: document.querySelector("#main-prompt").value,
      companion_context: document.querySelector("#companion-context").value,
      default_provider_id: Number(selectedProviderId) || null,
      default_model: selectedModel,
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
    const keyKind = document.querySelector("#key-kind").value;
    const key_value = document.querySelector("#key-value").value;
    const label = document.querySelector("#key-label").value;
    if (keyKind === "openai") {
      const payload = {
        label,
        base_url: document.querySelector("#provider-base-url").value,
        api_key: key_value,
        organization: document.querySelector("#provider-organization").value,
        project: document.querySelector("#provider-project").value,
        default_headers: document.querySelector("#provider-headers").value,
        default_query: document.querySelector("#provider-query").value,
        timeout_seconds: Number(document.querySelector("#provider-timeout").value) || null,
        max_retries: document.querySelector("#provider-retries").value === "" ? null : Number(document.querySelector("#provider-retries").value),
      };
      state.settings = await api("/api/settings/providers", { method: "POST", body: JSON.stringify(payload) });
      renderProviders();
      announce("تمت إضافة مزود OpenAI-compatible");
    } else {
      state.settings = await api("/api/settings/api-keys", { method: "POST", body: JSON.stringify({ key_value, label }) });
      renderKeys();
      announce("تمت إضافة مفتاح Gemini");
    }
    document.querySelector("#key-form").reset();
    renderKeyKindFields();
    document.querySelector("#key-value").value = "";
    document.querySelector("#key-label").value = "";
    populateModelSelect();
  });
}

function renderKeyKindFields() {
  const kind = document.querySelector("#key-kind")?.value || "gemini";
  const fields = document.querySelector("#openai-provider-fields");
  const baseUrl = document.querySelector("#provider-base-url");
  const label = document.querySelector("label[for='key-label']");
  const keyValue = document.querySelector("#key-value");
  if (!fields) return;
  fields.hidden = kind !== "openai";
  if (baseUrl) baseUrl.required = kind === "openai";
  if (label) label.textContent = kind === "openai" ? "اسم المزود" : "اسم المفتاح";
  if (keyValue) keyValue.required = true;
}

function populateModelSelect(nextProviderId = null) {
  const providerSelect = document.querySelector("#default-provider");
  const select = document.querySelector("#default-model");
  if (!select) return;
  const providers = state.settings?.providers || [];
  const desiredProviderId = nextProviderId ?? String(state.settings?.default_provider_id || "");
  if (providerSelect) {
    providerSelect.innerHTML = [
      `<option value="">Gemini المفاتيح القديمة</option>`,
      ...providers.map((provider) => `<option value="${provider.id}">${escapeHtml(provider.label)}</option>`),
    ].join("");
    providerSelect.value = desiredProviderId;
    providerSelect.onchange = () => populateModelSelect(providerSelect.value);
  }
  const selectedProviderId = providerSelect?.value || "";
  const provider = providers.find((item) => String(item.id) === selectedProviderId);
  const saved = state.settings?.default_model || "";
  const options = provider
    ? provider.models.map((model) => ({ name: model.model_id, display_name: model.display_name || model.model_id }))
    : [...state.models];
  if (!provider && saved && !options.some((model) => model.name === saved)) {
    options.unshift({ name: saved, display_name: saved.replace("models/", "") });
  }
  if (!options.length) {
    options.push(provider
      ? { name: "", display_name: "Fetch this provider's models first" }
      : { name: saved || "models/gemini-1.5-flash", display_name: saved || "gemini-1.5-flash" });
  }
  select.innerHTML = options
    .map((model) => `<option value="${escapeHtml(model.name)}">${escapeHtml(model.display_name)}</option>`)
    .join("");
  select.value = options.some((model) => model.name === saved) ? saved : options[0].name;
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

function renderProviders() {
  const container = document.querySelector("#provider-list");
  if (!container) return;
  const providers = state.settings?.providers || [];
  if (!providers.length) {
    container.innerHTML = `<div class="empty-state">لا توجد مزودات OpenAI-compatible بعد.</div>`;
    return;
  }
  container.innerHTML = providers
    .map((provider) => `
      <div class="key-row">
        <div>
          <strong>${escapeHtml(provider.label)}</strong>
          <div class="subtle">${escapeHtml(provider.base_url)}</div>
          <div class="subtle">${escapeHtml(provider.masked)} · ${provider.models.length} نموذج</div>
        </div>
        <div class="row-actions">
          <button class="compact" data-fetch-provider="${provider.id}">جلب النماذج</button>
          <button class="compact danger" data-delete-provider="${provider.id}">حذف</button>
        </div>
      </div>
    `)
    .join("");

  container.querySelectorAll("[data-fetch-provider]").forEach((button) => {
    button.addEventListener("click", async () => {
      announce("جار جلب نماذج المزود");
      state.settings = await api(`/api/settings/providers/${button.dataset.fetchProvider}/models/fetch`, { method: "POST", body: JSON.stringify({}) });
      const result = state.settings.fetch_result;
      populateModelSelect();
      renderProviders();
      announce(`تم الجلب: ${result.seen} موجود، ${result.added} جديد، ${result.updated} محدث`);
    });
  });

  container.querySelectorAll("[data-delete-provider]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/settings/providers/${button.dataset.deleteProvider}`, { method: "DELETE" });
      state.settings = await api("/api/settings");
      populateModelSelect();
      renderProviders();
      announce("تم حذف المزود");
    });
  });
}

async function renderCompanion() {
  setActiveNav("companion");
  state.settings = await api("/api/settings");
  if (!state.chatProviderId) state.chatProviderId = String(state.settings.default_provider_id || "");
  if (!state.chatProviderId) await ensureGeminiModelsLoaded();
  if (!state.chatModel) state.chatModel = state.settings.default_model || "models/gemini-1.5-flash";
  view.innerHTML = `
    <section class="chat-layout" aria-labelledby="chat-title">
      <div class="chat-head">
        <div>
          <h1 id="chat-title">رفيق الرفقاء</h1>
          <p class="subtle">جلسة مؤقتة لا تحفظ في قاعدة البيانات.</p>
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
      <div class="chat-controls" aria-label="إعدادات جلسة الرفيق">
        <div class="field chat-model">
          <label for="session-provider">مزود الجلسة</label>
          <select id="session-provider"></select>
        </div>
        <div class="field chat-model">
          <label for="session-model">نموذج الجلسة</label>
          <select id="session-model"></select>
        </div>
      </div>
    </section>
  `;
  populateSessionModel();
  renderMessages();
  document.querySelector("#session-provider").addEventListener("change", async (event) => {
    state.chatProviderId = event.target.value;
    state.chatModel = "";
    if (!state.chatProviderId) await ensureGeminiModelsLoaded();
    populateSessionModel();
  });
  document.querySelector("#session-model").addEventListener("change", (event) => {
    state.chatModel = event.target.value;
  });
  document.querySelector("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      document.querySelector("#chat-form").requestSubmit();
    }
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
  const providerSelect = document.querySelector("#session-provider");
  const select = document.querySelector("#session-model");
  if (!select) return;
  const providers = state.settings?.providers || [];
  if (providerSelect) {
    providerSelect.innerHTML = [
      `<option value="">Gemini legacy keys</option>`,
      ...providers.map((provider) => `<option value="${provider.id}">${escapeHtml(provider.label)}</option>`),
    ].join("");
    providerSelect.value = state.chatProviderId || "";
  }
  const provider = providers.find((item) => String(item.id) === (state.chatProviderId || ""));
  const models = modelOptionsForProvider(provider);
  const defaultModel = state.settings?.default_model || "";
  if (!state.chatModel) {
    if (defaultModel && models.some((model) => model.name === defaultModel)) {
      state.chatModel = defaultModel;
    } else {
      state.chatModel = models[0]?.name || defaultModel || "models/gemini-1.5-flash";
    }
  }
  if (provider && state.chatModel && !models.some((model) => model.name === state.chatModel)) {
    state.chatModel = models[0]?.name || "";
  }
  if (!provider && state.chatModel && !models.some((model) => model.name === state.chatModel)) {
    models.unshift({ name: state.chatModel, display_name: state.chatModel.replace("models/", "") });
  }
  select.innerHTML = models
    .map((model) => `<option value="${escapeHtml(model.name)}">${escapeHtml(model.display_name)}</option>`)
    .join("");
  if (!select.innerHTML) {
    const displayName = provider ? "Fetch this provider's models first" : state.chatModel.replace("models/", "");
    select.innerHTML = `<option value="${escapeHtml(state.chatModel)}">${escapeHtml(displayName)}</option>`;
  }
  select.value = state.chatModel;
}

function modelOptionsForProvider(provider) {
  const seen = new Set();
  const source = provider
    ? (provider.models || []).map((model) => ({
        name: model.model_id,
        display_name: model.display_name || model.model_id,
      }))
    : [...state.models];
  return source
    .filter((model) => {
      const name = model.name || model.model_id || "";
      if (!name || seen.has(name)) return false;
      seen.add(name);
      model.name = name;
      model.display_name = model.display_name || name.replace("models/", "");
      return true;
    })
    .sort((a, b) => a.display_name.localeCompare(b.display_name, "ar"));
}

async function ensureGeminiModelsLoaded() {
  if (state.models.length) return;
  try {
    const payload = await api("/api/settings/models/search", { method: "POST", body: JSON.stringify({}) });
    state.models = payload.models || [];
  } catch {
    state.models = [];
  }
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
    ...((message.statuses || []).length ? [`<details class="trace status-trace" open><summary>الحالة</summary><pre>${escapeHtml(message.statuses.join("\n"))}</pre></details>`] : []),
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
  if (state.chatProviderId !== "" && !state.chatModel) {
    showError(new Error("اجلب نماذج المزود أولا ثم اختر نموذجا."));
    return;
  }
  state.chat.push({ role: "model", text: "", thinking: "", roadmap: "", tools: [], statuses: [] });
  renderMessages();
  announce("رفيق الرفقاء يكتب الرد");
  const modelIndex = state.chat.length - 1;
  const modelMessage = state.chat[state.chat.length - 1];
  const history = state.chat
    .slice(0, -1)
    .slice(-15);
  const messages = history
    .filter((message, index) => {
      if (message.failed) return false;
      const nextMessage = history[index + 1];
      if (message.role === "user" && nextMessage?.role === "model" && nextMessage.failed) return false;
      return message.role === "user" || message.role === "model";
    })
    .map((message) => ({ role: message.role, text: cleanAssistantText(message.text || "") }))
    .filter((message) => message.role === "user" || message.text.trim());
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages,
        model: state.chatModel,
        provider_id: state.chatProviderId === "" ? 0 : Number(state.chatProviderId),
      }),
    });
    if (!response.ok || !response.body) throw new Error("تعذر بدء البث");
    await readEventStream(response, (event, payload) => {
      if (event === "status") {
        const text = payload.text || "";
        if (text) {
          modelMessage.statuses.push(text);
          announce(text);
        }
      }
      if (event === "delta") {
        if (!modelMessage.startedStreaming) {
          modelMessage.startedStreaming = true;
          announce("بدأ بث الرد");
        }
        modelMessage.text += payload.text || "";
      }
      if (event === "roadmap_delta") modelMessage.roadmap += payload.text || "";
      if (event === "thinking" || event === "thinking_delta") modelMessage.thinking += `${payload.text || ""}\n`;
      if (event === "tool_call") {
        modelMessage.tools.push(payload);
        announce(`استخدم أداة: ${payload.name || "أداة"}`);
      }
      if (event === "error") {
        modelMessage.failed = true;
        announce(payload.message || "حدث خطأ أثناء الرد", { error: true });
        modelMessage.text += `\n${payload.message}`;
      }
      if (event === "done" && !cleanAssistantText(modelMessage.text).trim() && !modelMessage.tools.length) {
        modelMessage.failed = true;
        modelMessage.text = "لم يرجع النموذج إجابة هذه المرة. جرّب إرسال السؤال مرة أخرى.";
      }
      updateMessageElement(modelIndex);
    });
    announce("انتهى رفيق الرفقاء من الرد");
  } catch (error) {
    modelMessage.failed = true;
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
