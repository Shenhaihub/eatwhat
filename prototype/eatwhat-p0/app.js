(() => {
  "use strict";

  const screens = [...document.querySelectorAll("[data-screen]")];
  const testParams = new URLSearchParams(window.location.search);
  const testConfig = {
    participantMode: testParams.get("mode") === "participant",
    loggedIn: testParams.get("login") === "1",
    aiSpeed: ["slow", "fast", "error"].includes(testParams.get("ai")) ? testParams.get("ai") : "slow",
    poiState: ["ready", "empty", "error"].includes(testParams.get("poi")) ? testParams.get("poi") : "ready"
  };
  const state = {
    screen: "home",
    previousScreen: "home",
    loggedIn: false,
    questionnaireStage: "base",
    answers: {},
    invalidated: false,
    aiRound: 0,
    aiAnswers: [],
    pendingAiAction: null,
    failedOnce: false,
    visibleResults: 1,
    selectedFood: "",
    source: "",
    locationSet: false
  };

  const answerLabels = {
    appetite: { light: "轻一点", normal: "正常一餐", hungry: "很饿" },
    avoidance: { none: "暂无忌口", seafood: "避开海鲜", meat: "不吃肉" },
    taste: { light: "清淡", spicy: "香辣", any: "口味不限" },
    branch: { soup: "想喝汤", dry: "不想喝汤", mild: "微辣", hot: "中辣以上", flexible: "辣度灵活" },
    budget: { under_20: "20 元以下", from_20_to_30: "20–30 元", over_30: "30 元以上" },
    explicit: { "麻辣烫": "麻辣烫", "牛肉面": "牛肉面", undecided: "还没想好" }
  };

  const aiQuestions = [
    { title: "最后更想要哪种用餐感觉？", options: ["热乎满足", "清爽轻松", "熟悉稳妥"] },
    { title: "今天愿意尝试一点新口味吗？", options: ["想尝试新的", "更想吃熟悉的", "都可以"] },
    { title: "如果要二选一，你更在意什么？", options: ["更好吃", "更省事", "更有饱腹感"] }
  ];

  const recommendations = [
    { name: "小碗菜", emoji: "🍚", reason: "选择灵活，能兼顾食量和你想要的稳妥感；具体门店价格需要另行确认。", tags: ["搭配灵活", "价格以门店为准"] },
    { name: "牛肉面", emoji: "🍜", reason: "热乎且有主食，适合想吃完整一餐的时候。", tags: ["热食", "饱腹"] },
    { name: "黄焖鸡", emoji: "🍗", reason: "口味明确、配米饭稳定，附近通常也容易找到。", tags: ["稳妥", "正餐"] },
    { name: "砂锅米线", emoji: "🥘", reason: "热汤和主食兼具，口味可以按偏好调整。", tags: ["热乎", "可调整"] },
    { name: "轻食饭碗", emoji: "🥗", reason: "想降低负担时，可以保留主食又方便控制份量。", tags: ["份量灵活", "清爽"] }
  ];

  const merchantNames = ["拾味小馆", "谷田稻香料理", "今天吃好饭", "街角热食铺", "一碗好味道"];

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function setFocus(screen) {
    requestAnimationFrame(() => {
      const heading = $(`[data-screen="${screen}"] h1`);
      if (heading) heading.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "auto" });
    });
  }

  function showScreen(name, options = {}) {
    const target = $(`[data-screen="${name}"]`);
    if (!target) return;
    state.previousScreen = state.screen;
    state.screen = name;
    screens.forEach((screen) => { screen.hidden = screen !== target; });
    document.body.classList.toggle("focus-mode", target.classList.contains("focus-screen"));
    window.location.hash = name;
    if (name === "questionnaire") renderQuestionnaire();
    if (name === "ai") renderAiQuestion();
    if (name === "results") renderResults();
    if (name === "location") renderLocation();
    if (name === "merchants") loadMerchants();
    if (!options.keepFocus) setFocus(name);
  }

  function labelFor(key, value) {
    return answerLabels[key]?.[value] || value || "未回答";
  }

  function collectForm(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  function formComplete(form) {
    return [...form.querySelectorAll("fieldset")].every((field) => field.querySelector("input:checked"));
  }

  function updateLoginUi() {
    $("#login-toggle").checked = state.loggedIn;
    $("#account-button").textContent = state.loggedIn ? "原型用户" : "登录";
    $("#mobile-account").lastChild.textContent = state.loggedIn ? "账户" : "我的";
  }

  function applyTestConfiguration() {
    document.body.classList.toggle("participant-mode", testConfig.participantMode);
    state.loggedIn = testConfig.loggedIn;
    $("#ai-speed").value = testConfig.aiSpeed;
    $("#poi-state").value = testConfig.poiState;
  }

  function selectDirectFood(food, source) {
    state.selectedFood = food;
    state.source = source;
    state.previousScreen = state.screen;
    $$('[data-selected-food]').forEach((el) => { el.textContent = food; });
    showScreen("location");
  }

  function branchConfig() {
    if (state.answers.taste === "light") {
      return {
        legend: "1. 清淡一点的话，今天想不想喝汤？",
        options: [["soup", "想喝汤", "更偏向汤面、米线或汤饭"], ["dry", "不想喝汤", "更偏向饭、拌面或轻食"], ["flexible", "都可以", "让后续判断决定"]]
      };
    }
    return {
      legend: "1. 如果食物可以做辣，你更能接受哪种程度？",
      options: [["mild", "微辣", "有一点刺激就好"], ["hot", "中辣以上", "今天可以吃得更带劲"], ["flexible", "都可以", "辣度不是决定因素"]]
    };
  }

  function renderBranchQuestion() {
    const config = branchConfig();
    const fieldset = $("#branch-question");
    fieldset.innerHTML = `<legend>${config.legend}</legend><div class="choice-grid">${config.options.map(([value, title, help]) => `<label class="choice-card"><input type="radio" name="branch" value="${value}" required><span><strong>${title}</strong><small>${help}</small></span></label>`).join("")}</div>`;
    if (state.answers.branch) {
      const saved = fieldset.querySelector(`input[value="${CSS.escape(state.answers.branch)}"]`);
      if (saved) saved.checked = true;
    }
  }

  function restoreInputs(form, keys) {
    keys.forEach((key) => {
      const value = state.answers[key];
      if (!value) return;
      const input = form.querySelector(`[name="${key}"][value="${CSS.escape(value)}"]`);
      if (input) input.checked = true;
    });
  }

  function renderQuestionnaire() {
    const base = $("#base-form");
    const adaptive = $("#adaptive-form");
    const isBase = state.questionnaireStage === "base";
    base.hidden = !isBase;
    adaptive.hidden = isBase;
    $("#question-title").textContent = isBase ? "先确认三个基础信息" : "根据前面的回答，再确认三个细节";
    if (isBase) restoreInputs(base, ["appetite", "avoidance", "taste"]);
    else {
      renderBranchQuestion();
      restoreInputs(adaptive, ["branch", "budget", "explicit"]);
    }
    updateAnswerSummary();
    updateCoverage();
  }

  function updateCoverage() {
    const baseCount = ["appetite", "avoidance", "taste"].filter((key) => state.answers[key]).length;
    const adaptiveCount = ["branch", "budget", "explicit"].filter((key) => state.answers[key]).length;
    const covered = Math.min(7, baseCount + (adaptiveCount ? 1 + adaptiveCount : 0));
    $("#coverage-label").textContent = `已了解 ${covered}/7 个方面`;
    $("#coverage-bar").style.width = `${(covered / 7) * 100}%`;
  }

  function answerEntries() {
    const entries = [
      ["食量", labelFor("appetite", state.answers.appetite)],
      ["需要避开", labelFor("avoidance", state.answers.avoidance)],
      ["口味", labelFor("taste", state.answers.taste)],
      [state.answers.taste === "light" ? "汤食偏好" : "辣度", labelFor("branch", state.answers.branch)],
      ["预算", labelFor("budget", state.answers.budget)],
      ["明确食物", labelFor("explicit", state.answers.explicit)]
    ];
    return entries.filter(([, value]) => value !== "未回答");
  }

  function updateAnswerSummary() {
    const entries = answerEntries();
    $("#answer-summary").innerHTML = entries.length
      ? entries.map(([key, value]) => `<div class="answer-chip"><small>${key}</small><strong>${value}</strong></div>`).join("")
      : '<p class="muted">回答后会显示在这里。</p>';
  }

  function showFormError(id) {
    const error = $(id);
    error.hidden = false;
    error.focus();
  }

  function submitBase(event) {
    event.preventDefault();
    if (!formComplete(event.currentTarget)) return showFormError("#base-error");
    $("#base-error").hidden = true;
    const previousTaste = state.answers.taste;
    Object.assign(state.answers, collectForm(event.currentTarget));
    if (previousTaste && previousTaste !== state.answers.taste && state.answers.branch) {
      delete state.answers.branch;
      state.invalidated = true;
      const notice = $("#branch-notice");
      notice.textContent = "你修改了口味偏好，原来的后续回答已失效；预算和明确食物等无冲突答案仍会保留。";
      notice.hidden = false;
    }
    state.questionnaireStage = "adaptive";
    renderQuestionnaire();
    $("#question-title").focus();
  }

  function submitAdaptive(event) {
    event.preventDefault();
    if (!formComplete(event.currentTarget)) return showFormError("#adaptive-error");
    $("#adaptive-error").hidden = true;
    Object.assign(state.answers, collectForm(event.currentTarget));
    updateAnswerSummary();
    if (state.answers.explicit !== "undecided") {
      selectDirectFood(state.answers.explicit, "user_selected");
      return;
    }
    if (!state.loggedIn) {
      $("#auth-summary").innerHTML = answerEntries().map(([key, value]) => `<span>${key}：${value}</span>`).join("");
      showScreen("auth");
      return;
    }
    startAi();
  }

  function editBaseAnswers() {
    state.questionnaireStage = "base";
    showScreen("questionnaire");
  }

  function startAi() {
    state.aiRound = 0;
    state.aiAnswers = [];
    state.failedOnce = false;
    showScreen("ai");
  }

  function renderAiQuestion() {
    const question = aiQuestions[state.aiRound] || aiQuestions[2];
    $("#ai-round-label").textContent = `第 ${Math.min(state.aiRound + 1, 3)}/最多 3 轮`;
    $("#ai-question-title").textContent = question.title;
    $("#ai-options").innerHTML = question.options.map((option) => `<label class="choice-card"><input type="radio" name="ai_answer" value="${option}" required><span><strong>${option}</strong></span></label>`).join("");
    $("#ai-form").hidden = false;
    $("#ai-wait").hidden = true;
    $("#ai-request-error").hidden = true;
    $("#ai-error").hidden = true;
    $("#ai-known-summary").innerHTML = [
      ...answerEntries().map(([key, value]) => `<div class="answer-chip"><small>${key}</small><strong>${value}</strong></div>`),
      ...state.aiAnswers.map((value, index) => `<div class="answer-chip"><small>AI 追问 ${index + 1}</small><strong>${value}</strong></div>`)
    ].join("");
  }

  function submitAi(event) {
    event.preventDefault();
    const selected = event.currentTarget.querySelector("input:checked");
    if (!selected) return showFormError("#ai-error");
    $("#ai-error").hidden = true;
    state.pendingAiAction = { answer: selected.value, requestId: `prototype-${state.aiRound + 1}` };
    runAiRequest();
  }

  function runAiRequest() {
    const speed = $("#ai-speed").value;
    const pending = state.pendingAiAction;
    $("#ai-submit").disabled = true;
    $("#saved-ai-answer").textContent = `✓ 你的回答：${pending.answer}`;
    $("#ai-wait").hidden = true;
    $("#ai-request-error").hidden = true;

    const waitTimer = window.setTimeout(() => {
      $("#ai-form").hidden = true;
      $("#ai-wait").hidden = false;
      const finalStage = state.aiRound >= 2;
      $("#ai-stage-title").textContent = finalStage ? "正在综合全部回答" : "正在根据你的回答准备下一题";
      $("#ai-stage-help").textContent = finalStage ? "接下来会一次准备 5 个推荐结果。你的回答已保存。" : "你的回答已保存；这次等待不会多算一轮。";
    }, 800);

    const delay = speed === "fast" ? 350 : 1800;
    window.setTimeout(() => {
      window.clearTimeout(waitTimer);
      if (speed === "error" && !state.failedOnce) {
        state.failedOnce = true;
        $("#ai-form").hidden = true;
        $("#ai-wait").hidden = true;
        $("#ai-request-error").hidden = false;
        $("#ai-request-error").focus();
        $("#ai-submit").disabled = false;
        return;
      }
      completeAiRequest(pending.answer);
    }, delay);
  }

  function completeAiRequest(answer) {
    state.aiAnswers[state.aiRound] = answer;
    state.pendingAiAction = null;
    state.aiRound += 1;
    $("#ai-submit").disabled = false;
    if (state.aiRound >= 3) {
      showFinalGeneration();
      return;
    }
    renderAiQuestion();
    $("#ai-question-title").focus();
  }

  function showFinalGeneration() {
    $("#ai-form").hidden = true;
    $("#ai-wait").hidden = false;
    $("#ai-stage-title").textContent = "正在准备 5 个推荐结果";
    $("#ai-stage-help").textContent = "结果会按 1→3→5 展示，减少一次面对太多选择。";
    window.setTimeout(() => {
      state.visibleResults = 1;
      showScreen("results");
    }, $("#ai-speed").value === "fast" ? 350 : 1200);
  }

  function renderResults() {
    const shown = recommendations.slice(0, state.visibleResults);
    $("#recommendation-list").innerHTML = shown.map((item, index) => `
      <article class="recommendation-card">
        <div class="rank-badge" aria-label="第 ${index + 1} 个候选">${index === 0 ? item.emoji : index + 1}</div>
        <div>
          <h2>${item.name}</h2>
          <p>${item.reason}</p>
          <div class="tag-list">${item.tags.map((tag) => `<span class="tag">${tag}</span>`).join("")}</div>
        </div>
        <button class="button ${index === 0 ? "button-primary" : "button-secondary"}" type="button" data-result-food="${item.name}">找附近</button>
      </article>`).join("");
    $("#result-count").textContent = `当前显示 ${state.visibleResults}/5 · 展开不会再次调用 AI`;
    $("#more-results").hidden = state.visibleResults >= 5;
    if (state.visibleResults < 5) $("#more-results").textContent = "更多推荐（再看 2 个）";
  }

  function showMoreResults() {
    state.visibleResults = Math.min(5, state.visibleResults + 2);
    renderResults();
    $("#result-count").textContent = `已显示 ${state.visibleResults}/5 · 没有产生新的 AI 请求`;
  }

  function renderLocation() {
    $$('[data-selected-food]').forEach((el) => { el.textContent = state.selectedFood || "这道食物"; });
    $("#location-current").hidden = !state.locationSet;
    $("#location-methods").hidden = state.locationSet;
    $("#permission-simulator").hidden = true;
    $("#location-denied").hidden = true;
  }

  function acceptLocation() {
    state.locationSet = true;
    showScreen("merchants");
  }

  function renderMerchantCards() {
    $("#merchant-list").innerHTML = merchantNames.map((name, index) => `
      <article class="merchant-card">
        <h2>${index + 1}. ${name} · ${state.selectedFood}</h2>
        <div class="merchant-meta"><span>${360 + index * 220} 米</span><span>匹配“${state.selectedFood}”</span>${index % 2 === 0 ? "<span>营业状态未知</span>" : ""}</div>
        <address>武汉市洪山区原型示例地址 ${index + 1} 号</address>
        <button class="button button-secondary merchant-open" type="button">在地图中打开</button>
      </article>`).join("");
  }

  function loadMerchants() {
    $$('[data-selected-food]').forEach((el) => { el.textContent = state.selectedFood || "食物"; });
    $("#poi-loading").hidden = false;
    $("#merchant-list").hidden = true;
    $("#poi-empty").hidden = true;
    $("#poi-error").hidden = true;
    $("#poi-source").hidden = true;
    $("#wrap-up").hidden = true;
    window.setTimeout(() => {
      $("#poi-loading").hidden = true;
      const poiState = $("#poi-state").value;
      if (poiState === "empty") {
        $("#poi-empty").hidden = false;
        $("#poi-empty").focus();
      } else if (poiState === "error") {
        $("#poi-error").hidden = false;
        $("#poi-error").focus();
      } else {
        renderMerchantCards();
        $("#merchant-list").hidden = false;
        $("#poi-source").hidden = false;
        $("#wrap-up").hidden = false;
      }
    }, 650);
  }

  function resetPrototype() {
    Object.assign(state, {
      screen: "home", previousScreen: "home", loggedIn: false, questionnaireStage: "base", answers: {}, invalidated: false,
      aiRound: 0, aiAnswers: [], pendingAiAction: null, failedOnce: false, visibleResults: 1, selectedFood: "", source: "", locationSet: false
    });
    $$('input[type="radio"]').forEach((input) => { input.checked = false; });
    $("#branch-notice").hidden = true;
    $("#poi-state").value = "ready";
    $("#ai-speed").value = "slow";
    applyTestConfiguration();
    updateLoginUi();
    showScreen("home");
  }

  document.addEventListener("click", (event) => {
    const go = event.target.closest("[data-go]");
    if (go) {
      const target = go.dataset.go;
      if (target === "questionnaire") {
        state.questionnaireStage = "base";
      }
      showScreen(target);
      return;
    }
    const direct = event.target.closest("[data-food-direct]");
    if (direct) selectDirectFood(direct.dataset.foodDirect, direct.dataset.source);
    const result = event.target.closest("[data-result-food]");
    if (result) selectDirectFood(result.dataset.resultFood, "ai_recommended");
  });

  $("#base-form").addEventListener("submit", submitBase);
  $("#adaptive-form").addEventListener("submit", submitAdaptive);
  $("#edit-base").addEventListener("click", editBaseAnswers);
  $("#auth-back").addEventListener("click", editBaseAnswers);
  $("#ai-edit-answers").addEventListener("click", editBaseAnswers);
  $("#results-edit").addEventListener("click", editBaseAnswers);
  $("#ai-form").addEventListener("submit", submitAi);
  $("#retry-ai").addEventListener("click", runAiRequest);
  $("#more-results").addEventListener("click", showMoreResults);

  ["#mock-login", "#mock-register"].forEach((id) => {
    $(id).addEventListener("click", () => { state.loggedIn = true; updateLoginUi(); startAi(); });
  });

  $("#login-toggle").addEventListener("change", (event) => { state.loggedIn = event.target.checked; updateLoginUi(); });
  $("#account-button").addEventListener("click", () => { state.loggedIn = !state.loggedIn; updateLoginUi(); });
  $("#mobile-account").addEventListener("click", () => { state.loggedIn = !state.loggedIn; updateLoginUi(); });
  $("#reset-prototype").addEventListener("click", resetPrototype);

  $("#location-back").addEventListener("click", () => showScreen(state.source === "ai_recommended" ? "results" : "home"));
  $("#use-current-location").addEventListener("click", () => { $("#permission-simulator").hidden = false; $("#permission-title").focus(); });
  $("#allow-location").addEventListener("click", acceptLocation);
  $("#deny-location").addEventListener("click", () => {
    $("#permission-simulator").hidden = true;
    $("#location-denied").hidden = false;
    $("#location-denied").focus();
  });
  $("#use-demo-location").addEventListener("click", acceptLocation);
  $("#search-place").addEventListener("click", () => {
    if (!$("#place-search").value.trim()) {
      $("#place-search").setCustomValidity("请输入地点关键词");
      $("#place-search").reportValidity();
      return;
    }
    $("#place-search").setCustomValidity("");
    acceptLocation();
  });
  $("#reuse-location").addEventListener("click", () => showScreen("merchants"));
  $("#change-location").addEventListener("click", () => { state.locationSet = false; renderLocation(); });
  $("#retry-poi").addEventListener("click", loadMerchants);
  $("#demo-poi").addEventListener("click", () => { $("#poi-state").value = "ready"; loadMerchants(); });
  $("#expand-range").addEventListener("click", () => { $("#poi-state").value = "ready"; loadMerchants(); });
  $("#close-wrap-up").addEventListener("click", () => { $("#wrap-up").hidden = true; });

  $$(".feedback-button, .wrap-submit").forEach((button) => {
    button.addEventListener("click", () => {
      $("#wrap-message").textContent = "已记录原型操作。这个提交不影响推荐或其他可选项。";
      $("#wrap-message").hidden = false;
    });
  });

  window.addEventListener("online", () => { $("#offline-banner").hidden = true; });
  window.addEventListener("offline", () => { $("#offline-banner").hidden = false; });
  window.addEventListener("hashchange", () => {
    const requested = window.location.hash.slice(1);
    if (requested && requested !== state.screen && $(`[data-screen="${requested}"]`)) showScreen(requested, { keepFocus: false });
  });

  applyTestConfiguration();
  updateLoginUi();
  showScreen(window.location.hash.slice(1) || "home");
})();
