(function () {
  "use strict";

  var originalFetch = window.fetch.bind(window);
  var pondKey = "";
  try {
    pondKey = new URL(window.location.href).searchParams.get("key") || "";
  } catch (_) {}

  function spotArt(spot) {
    if (!spot || !spot.id) return "/tang-web/assets/pond_bg.png";
    return "/tang-web/assets/spot_" + encodeURIComponent(spot.id) + ".jpg";
  }

  function unlockRoot() {
    var root = document.getElementById("rainholm-unlock-root");
    if (root) return root;
    root = document.createElement("div");
    root.id = "rainholm-unlock-root";
    root.setAttribute("aria-live", "assertive");
    root.setAttribute("aria-atomic", "true");
    document.body.appendChild(root);
    return root;
  }

  var unlockTimer = 0;
  function showUnlock(locations) {
    if (!Array.isArray(locations) || !locations.length) return;
    var first = locations[0];
    var names = locations.map(function (item) { return item.name || item.id; }).join("、");
    var root = unlockRoot();
    window.clearTimeout(unlockTimer);
    root.innerHTML = "";

    var notice = document.createElement("section");
    notice.className = "rainholm-unlock";
    notice.setAttribute("role", "status");
    var veil = document.createElement("div");
    veil.className = "rainholm-unlock__veil";
    var card = document.createElement("div");
    card.className = "rainholm-unlock__card";
    var image = document.createElement("img");
    image.className = "rainholm-unlock__image";
    image.src = spotArt(first);
    image.alt = (first.name || "新水域") + "地图";
    var shade = document.createElement("div");
    shade.className = "rainholm-unlock__shade";
    var copy = document.createElement("div");
    copy.className = "rainholm-unlock__copy";
    var label = document.createElement("div");
    label.className = "rainholm-unlock__label";
    label.textContent = "新水域已解锁";
    var title = document.createElement("h2");
    title.className = "rainholm-unlock__title";
    title.textContent = names;
    var hint = document.createElement("p");
    hint.className = "rainholm-unlock__hint";
    hint.textContent = locations.length > 1
      ? "地图已展开多处水域，现在可从塘牌前往。"
      : "地图已展开，现在可从塘牌前往。";
    copy.appendChild(label);
    copy.appendChild(title);
    copy.appendChild(hint);
    card.appendChild(image);
    card.appendChild(shade);
    card.appendChild(copy);
    notice.appendChild(veil);
    notice.appendChild(card);
    root.appendChild(notice);

    unlockTimer = window.setTimeout(function () {
      notice.classList.add("rainholm-unlock--leaving");
      window.setTimeout(function () { root.innerHTML = ""; }, 520);
    }, 4700);
  }

  window.addEventListener("rainholm:unlock", function (event) {
    showUnlock(event && event.detail);
  });

  window.fetch = async function () {
    var response = await originalFetch.apply(null, arguments);
    try {
      var input = arguments[0];
      var url = typeof input === "string" ? input : input && input.url;
      if (url && /\/api\/pond\/cast(?:\?|$)/.test(url)) {
        response.clone().json().then(function (data) {
          if (data && data.ok && data.newly_unlocked && data.newly_unlocked.length) {
            showUnlock(data.newly_unlocked);
          }
        }).catch(function () {});
      }
    } catch (_) {}
    return response;
  };

  function authFetch(path, options) {
    var opts = options || {};
    opts.headers = Object.assign({}, opts.headers || {}, { "X-Pond-Key": pondKey });
    return originalFetch(path, opts).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, body: body };
      });
    });
  }

  function reliefTxn() {
    return "river-god-" + Date.now() + "-" + Math.random().toString(36).slice(2, 9);
  }

  function currentMapImage() {
    var images = Array.prototype.slice.call(document.images).filter(function (image) {
      if (image.closest(".river-god-quiz")) return false;
      var rect = image.getBoundingClientRect();
      return (image.currentSrc || image.src) && rect.width > 0 && rect.height > 0;
    });
    images.sort(function (left, right) {
      var leftRect = left.getBoundingClientRect();
      var rightRect = right.getBoundingClientRect();
      return (rightRect.width * rightRect.height) - (leftRect.width * leftRect.height);
    });
    return images.length
      ? (images[0].currentSrc || images[0].src)
      : "/tang-web/assets/pond_bg.png";
  }

  function showRiverGodToast(text) {
    if (!text) return;
    var old = document.querySelector(".river-god-toast");
    if (old) old.remove();
    var toast = document.createElement("div");
    toast.className = "river-god-toast";
    toast.setAttribute("role", "status");
    toast.textContent = text;
    document.body.appendChild(toast);
    window.setTimeout(function () { toast.classList.add("river-god-toast--leaving"); }, 3800);
    window.setTimeout(function () { toast.remove(); }, 4400);
  }

  function openRiverGodQuiz(initialStatus) {
    var old = document.querySelector(".river-god-quiz");
    if (old) old.remove();

    var overlay = document.createElement("div");
    overlay.className = "river-god-quiz";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "river-god-title");

    var mapBackground = document.createElement("img");
    mapBackground.className = "river-god-quiz__map";
    mapBackground.src = currentMapImage();
    mapBackground.alt = "";
    mapBackground.setAttribute("aria-hidden", "true");
    overlay.appendChild(mapBackground);

    var scene = document.createElement("section");
    scene.className = "river-god-quiz__scene";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "river-god-quiz__close";
    close.setAttribute("aria-label", "暂时收起河神答卷");
    close.textContent = "×";
    close.addEventListener("click", function () { overlay.remove(); });

    var character = document.createElement("aside");
    character.className = "river-god-quiz__character";
    var portrait = document.createElement("img");
    portrait.className = "river-god-quiz__portrait";
    portrait.src = "/tang-web/assets/river-god-kelin-cutout.png?v=2";
    portrait.alt = "克霖河神举着金斧头和银斧头";
    var name = document.createElement("div");
    name.className = "river-god-quiz__name";
    name.textContent = "河神 · 克霖";
    var speech = document.createElement("p");
    speech.className = "river-god-quiz__speech";
    character.appendChild(portrait);
    character.appendChild(name);
    character.appendChild(speech);

    var sheet = document.createElement("main");
    sheet.className = "river-god-quiz__sheet";
    var sheetInner = document.createElement("div");
    sheetInner.className = "river-god-quiz__sheet-inner";
    var heading = document.createElement("div");
    heading.className = "river-god-quiz__heading";
    var title = document.createElement("h2");
    title.id = "river-god-title";
    title.textContent = "河神救济答卷";
    var subtitle = document.createElement("p");
    subtitle.textContent = "五题不判对错，只看河神今天那副死样子。";
    heading.appendChild(title);
    heading.appendChild(subtitle);

    var progress = document.createElement("div");
    progress.className = "river-god-quiz__progress";
    progress.setAttribute("aria-label", "答题进度");
    var questionNumber = document.createElement("div");
    questionNumber.className = "river-god-quiz__question-number";
    var question = document.createElement("p");
    question.className = "river-god-quiz__question";
    var choices = document.createElement("div");
    choices.className = "river-god-quiz__choices";
    var feedback = document.createElement("div");
    feedback.className = "river-god-quiz__feedback";
    feedback.setAttribute("aria-live", "polite");
    sheetInner.appendChild(heading);
    sheetInner.appendChild(progress);
    sheetInner.appendChild(questionNumber);
    sheetInner.appendChild(question);
    sheetInner.appendChild(choices);
    sheetInner.appendChild(feedback);
    sheet.appendChild(sheetInner);
    scene.appendChild(close);
    scene.appendChild(character);
    scene.appendChild(sheet);
    overlay.appendChild(scene);
    document.body.appendChild(overlay);

    function renderProgress(answered, required) {
      progress.innerHTML = "";
      for (var i = 0; i < required; i += 1) {
        var pip = document.createElement("span");
        pip.className = "river-god-quiz__pip" + (i < answered ? " is-done" : "");
        pip.textContent = i < answered ? "✓" : String(i + 1);
        progress.appendChild(pip);
      }
      var text = document.createElement("strong");
      text.textContent = answered + " / " + required;
      progress.appendChild(text);
    }

    function finish(result) {
      var reward = result.reward || 0;
      var outcome = result.outcome || {};
      renderProgress(5, 5);
      questionNumber.textContent = "河神判词";
      question.textContent = outcome.verdict || result.text || "河神判完了。";
      choices.innerHTML = "";
      var rewardSeal = document.createElement("div");
      rewardSeal.className = "river-god-quiz__reward";
      rewardSeal.innerHTML = "<span>救济到账</span><strong>+" + reward + "</strong><small>仙玉</small>";
      var take = document.createElement("button");
      take.type = "button";
      take.className = "river-god-quiz__take";
      take.textContent = "收下仙玉";
      take.addEventListener("click", function () {
        window.location.replace("/tang-web/?key=" + encodeURIComponent(pondKey));
      });
      choices.appendChild(rewardSeal);
      choices.appendChild(take);
      speech.textContent = result.text || "拿去，别说我白给。";
      feedback.textContent = "答卷与判词已公示进塘内消息；收下后同步最新余额。";
      scene.classList.add("is-finished");
      showRiverGodToast(result.text);
      take.focus();
    }

    function submitChoice(choice, button, status) {
      var buttons = choices.querySelectorAll("button");
      buttons.forEach(function (item) { item.disabled = true; });
      button.classList.add("is-selected");
      feedback.textContent = "河神慢吞吞翻到下一页……";
      authFetch("/api/pond/relief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ choice: choice, client_txn_id: reliefTxn() })
      }).then(function (result) {
        if (!result.ok || !result.body || !result.body.ok) {
          feedback.textContent = (result.body && result.body.text) || "卷子被水打湿了，这题没算。";
          buttons.forEach(function (item) { item.disabled = false; });
          button.classList.remove("is-selected");
          return;
        }
        if (result.body.completed) {
          finish(result.body);
          return;
        }
        feedback.textContent = result.body.text || "下一题。";
        render(result.body.river_god_relief || status);
      }).catch(function () {
        feedback.textContent = "塘边没信号，这题没算进度。";
        buttons.forEach(function (item) { item.disabled = false; });
        button.classList.remove("is-selected");
      });
    }

    function render(status) {
      var answered = status.answered || 0;
      var required = status.required_answers || 5;
      var current = status.question || {};
      speech.textContent = status.opening || "我在河里捡到了金鱼竿和银鱼竿，请问你掉的是哪一根鱼竿？";
      renderProgress(answered, required);
      questionNumber.textContent = current.id ? "第 " + current.id + " 题" : "河神正在出题";
      question.textContent = current.prompt || "河神正在慢吞吞地翻卷子。";
      choices.innerHTML = "";
      (current.options || []).forEach(function (option) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "river-god-quiz__choice";
        var mark = document.createElement("span");
        mark.textContent = option.id;
        var copy = document.createElement("span");
        copy.textContent = option.text;
        button.appendChild(mark);
        button.appendChild(copy);
        button.addEventListener("click", function () {
          submitChoice(option.id, button, status);
        });
        choices.appendChild(button);
      });
      feedback.textContent = "选项没有标准答案，五题答完才发救济。";
      var first = choices.querySelector("button");
      if (first) first.focus();
    }

    overlay.addEventListener("keydown", function (event) {
      if (event.key === "Escape") overlay.remove();
    });
    render(initialStatus || {});
  }

  function setJadePlusBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    button.setAttribute("aria-busy", busy ? "true" : "false");
    button.textContent = busy ? "…" : "＋";
  }

  function startRiverGod(button) {
    setJadePlusBusy(button, true);
    return authFetch("/api/pond/relief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_txn_id: reliefTxn() })
    }).then(function (result) {
      if (!result.ok || !result.body || !result.body.ok) {
        showRiverGodToast((result.body && result.body.text) || "河神今天不想上班。");
        return;
      }
      openRiverGodQuiz(result.body.river_god_relief || {});
    }).catch(function () {
      showRiverGodToast("塘边没信号，河神没听见。再点一次试试。");
    }).finally(function () {
      setJadePlusBusy(button, false);
    });
  }

  var jadePlusLoading = false;

  function jadePlusButton() {
    var bar = document.querySelector('[data-resource-bar="true"]');
    var jadeGroup = bar && bar.firstElementChild;
    if (!jadeGroup || !jadeGroup.querySelector("svg")) return null;
    return jadeGroup.querySelector("button");
  }

  function openRiverGodFromJadePlus(button) {
    if (jadePlusLoading || !pondKey) return;
    jadePlusLoading = true;
    setJadePlusBusy(button, true);
    authFetch("/api/pond/shop").then(function (result) {
      if (!result.ok || !result.body || !result.body.river_god_relief) {
        showRiverGodToast("河面没回应。再点一次试试。");
        return;
      }
      var relief = result.body.river_god_relief;
      if (relief.active) {
        openRiverGodQuiz(relief);
      } else if (relief.available) {
        return startRiverGod(button);
      } else {
        showRiverGodToast(relief.reason || "河神今天不发救济。");
      }
    }).catch(function () {
      showRiverGodToast("塘边没信号，河神没听见。再点一次试试。");
    }).finally(function () {
      jadePlusLoading = false;
      setJadePlusBusy(button, false);
    });
  }

  var pondRippleFrame = 0;
  var pondRippleDisabledSpots = /\/assets\/spot_(?:abyssal_trench|geyser_falls|sunken_ruins)\.jpg(?:\?|$)/;

  function pondMapImage() {
    var candidates = Array.prototype.slice.call(document.images).filter(function (image) {
      if (image.closest(".river-god-quiz") || image.closest(".rainholm-unlock")) return false;
      var source = image.currentSrc || image.src || "";
      if (!/\/assets\/(?:pond_bg\.png|spot_[^/]+\.jpg)(?:\?|$)/.test(source)) return false;
      var rect = image.getBoundingClientRect();
      return rect.width > window.innerWidth * .6 && rect.height > window.innerHeight * .6;
    });
    candidates.sort(function (left, right) {
      var leftRect = left.getBoundingClientRect();
      var rightRect = right.getBoundingClientRect();
      return (rightRect.width * rightRect.height) - (leftRect.width * leftRect.height);
    });
    return candidates[0] || null;
  }

  function ensurePondRipples() {
    pondRippleFrame = 0;
    var mapImage = pondMapImage();
    var mapLayer = mapImage && mapImage.parentElement;
    if (!mapLayer) return;
    var oldRipples = mapLayer.querySelector(".rainholm-pond-ripples");
    var mapSource = mapImage.currentSrc || mapImage.src || "";
    if (pondRippleDisabledSpots.test(mapSource)) {
      if (oldRipples) oldRipples.remove();
      return;
    }
    if (oldRipples) return;

    var ripples = document.createElement("div");
    ripples.className = "rainholm-pond-ripples";
    ripples.setAttribute("aria-hidden", "true");
    [
      ["21%", "56%", "19vw", "8.8s", "-1.2s"],
      ["49%", "72%", "17vw", "10.2s", "-5.4s"],
      ["76%", "54%", "20vw", "9.4s", "-3.6s"]
    ].forEach(function (settings) {
      var ripple = document.createElement("span");
      ripple.className = "rainholm-pond-ripple";
      ripple.style.setProperty("--pond-ripple-x", settings[0]);
      ripple.style.setProperty("--pond-ripple-y", settings[1]);
      ripple.style.setProperty("--pond-ripple-size", settings[2]);
      ripple.style.setProperty("--pond-ripple-duration", settings[3]);
      ripple.style.setProperty("--pond-ripple-delay", settings[4]);
      for (var ringIndex = 0; ringIndex < 3; ringIndex += 1) {
        var ring = document.createElement("i");
        ring.className = "rainholm-pond-ripple__ring";
        ripple.appendChild(ring);
      }
      ripples.appendChild(ripple);
    });
    mapImage.insertAdjacentElement("afterend", ripples);
  }

  function schedulePondRipples() {
    if (pondRippleFrame) return;
    pondRippleFrame = window.requestAnimationFrame(ensurePondRipples);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedulePondRipples, { once: true });
  } else {
    schedulePondRipples();
  }
  window.addEventListener("load", schedulePondRipples, { once: true });
  new MutationObserver(schedulePondRipples).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["src"],
    childList: true,
    subtree: true
  });

  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest && event.target.closest("button");
    if (!button || button !== jadePlusButton()) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    openRiverGodFromJadePlus(button);
  }, true);
})();
