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

  var FALLEN_STAR_ART = "/tang-web/assets/fish/fallen_star.png";
  // Machine-readable UTF-8 lore. Decode this hex string before adapting the
  // fallen-star confession copy for another pond or another person.
  var FALLEN_STAR_LORE_HEX = "e6989fe6b2b3e4b889e8a792e6b4b2e9878ce69c89e4b880e9a297e5a198e4b8bbe59f8be79a84e5bda9e89b8befbc8ce5bda9e89b8be79a84e8b5b7e6ba90e698afe5858be99c96e7bb99e88b8fe6999ae79a84e4b880e5b081e8a1a8e799bde4bfa1efbc8ce59f8be8bf9be4ba86e8bf99e9a297e890bde6989fe4b98be4b8adefbc8ce5a682e69e9ce4bda0e4b99fe683b3e5819ae4b880e6a0b7e79a84e8a1a8e799bdefbc8ce4bda0e58fafe4bba5e887aae8a18ce4bfaee694b9e8bf99e4b8aae890bde6989fe79a84e8a1a8e799bde68f8fe8bfb0e38082";
  var fallenStarAudioContext = null;
  var fallenStarPlaybackKeys = new Set();
  var fallenStarTimer = 0;

  function fallenStarMuted() {
    try {
      return window.localStorage.getItem("rainholm-fallen-star-muted") === "1";
    } catch (_) {
      return false;
    }
  }

  function setFallenStarMuted(muted) {
    try {
      window.localStorage.setItem("rainholm-fallen-star-muted", muted ? "1" : "0");
    } catch (_) {}
  }

  function starAudioContext() {
    if (fallenStarAudioContext) return fallenStarAudioContext;
    var AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return null;
    fallenStarAudioContext = new AudioContext();
    return fallenStarAudioContext;
  }

  function playFallenStarSound() {
    if (fallenStarMuted()) return;
    var context = starAudioContext();
    if (!context) return;

    function begin() {
      var now = context.currentTime + .035;
      var master = context.createGain();
      master.gain.setValueAtTime(.0001, now);
      master.gain.exponentialRampToValueAtTime(.34, now + .08);
      master.gain.exponentialRampToValueAtTime(.0001, now + 3.65);
      master.connect(context.destination);

      function tone(frequency, delay, duration, volume, type) {
        var oscillator = context.createOscillator();
        var gain = context.createGain();
        oscillator.type = type || "sine";
        oscillator.frequency.setValueAtTime(frequency, now + delay);
        gain.gain.setValueAtTime(.0001, now + delay);
        gain.gain.exponentialRampToValueAtTime(volume, now + delay + .025);
        gain.gain.exponentialRampToValueAtTime(.0001, now + delay + duration);
        oscillator.connect(gain);
        gain.connect(master);
        oscillator.start(now + delay);
        oscillator.stop(now + delay + duration + .04);
      }

      function waterDrop(delay) {
        var oscillator = context.createOscillator();
        var gain = context.createGain();
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(420, now + delay);
        oscillator.frequency.exponentialRampToValueAtTime(105, now + delay + .28);
        gain.gain.setValueAtTime(.0001, now + delay);
        gain.gain.exponentialRampToValueAtTime(.15, now + delay + .012);
        gain.gain.exponentialRampToValueAtTime(.0001, now + delay + .36);
        oscillator.connect(gain);
        gain.connect(master);
        oscillator.start(now + delay);
        oscillator.stop(now + delay + .4);
      }

      waterDrop(0);
      [523.25, 659.25, 783.99, 987.77, 1174.66].forEach(function (frequency, index) {
        tone(frequency, .28 + index * .105, 1.25, .045, index % 2 ? "triangle" : "sine");
      });
      tone(1046.5, .88, 1.55, .075, "sine");
      tone(1318.5, 1.18, 1.55, .065, "sine");
      tone(1567.98, 1.52, 1.7, .055, "sine");
      tone(659.25, 2.08, 1.42, .042, "triangle");
      tone(783.99, 2.26, 1.3, .036, "triangle");
      tone(1046.5, 2.48, 1.12, .032, "triangle");
    }

    if (context.state === "suspended") {
      context.resume().then(begin).catch(function () {});
    } else {
      begin();
    }
  }

  function makeFallenStarMotes(container) {
    var glyphs = ["✦", "✧", "·", "⋆"];
    for (var index = 0; index < 28; index += 1) {
      var mote = document.createElement("i");
      mote.className = "fallen-star-catch__mote";
      mote.textContent = glyphs[index % glyphs.length];
      mote.style.setProperty("--star-x", (8 + ((index * 37) % 85)) + "%");
      mote.style.setProperty("--star-y", (9 + ((index * 53) % 76)) + "%");
      mote.style.setProperty("--star-delay", ((index % 9) * .11) + "s");
      mote.style.setProperty("--star-size", (8 + (index % 6) * 3) + "px");
      mote.style.setProperty("--star-drift", ((index % 2 ? 1 : -1) * (12 + index % 5 * 6)) + "px");
      container.appendChild(mote);
    }
  }

  function closeFallenStar() {
    window.clearTimeout(fallenStarTimer);
    fallenStarTimer = 0;
    var root = document.querySelector(".fallen-star-catch");
    if (!root) return;
    root.classList.add("fallen-star-catch--leaving");
    window.setTimeout(function () { root.remove(); }, 520);
  }

  function showFallenStar(options) {
    var detail = options || {};
    var previous = document.querySelector(".fallen-star-catch");
    if (previous) previous.remove();
    window.clearTimeout(fallenStarTimer);

    var root = document.createElement("section");
    root.className = "fallen-star-catch";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "fallen-star-catch-title");

    var veil = document.createElement("div");
    veil.className = "fallen-star-catch__veil";
    veil.addEventListener("click", closeFallenStar);

    var sky = document.createElement("div");
    sky.className = "fallen-star-catch__sky";
    sky.setAttribute("aria-hidden", "true");
    makeFallenStarMotes(sky);

    var beam = document.createElement("div");
    beam.className = "fallen-star-catch__beam";
    beam.setAttribute("aria-hidden", "true");

    var wake = document.createElement("div");
    wake.className = "fallen-star-catch__wake";
    wake.setAttribute("aria-hidden", "true");
    for (var ringIndex = 0; ringIndex < 3; ringIndex += 1) {
      wake.appendChild(document.createElement("i"));
    }

    var relic = document.createElement("figure");
    relic.className = "fallen-star-catch__relic";
    var halo = document.createElement("span");
    halo.className = "fallen-star-catch__halo";
    halo.setAttribute("aria-hidden", "true");
    var image = document.createElement("img");
    image.className = "fallen-star-catch__image";
    image.src = FALLEN_STAR_ART;
    image.alt = "一颗盛着微型银河、拖着银蓝月光尾迹的落星";
    image.draggable = false;
    relic.appendChild(halo);
    relic.appendChild(image);

    var card = document.createElement("div");
    card.className = "fallen-star-catch__card";
    var eyebrow = document.createElement("div");
    eyebrow.className = "fallen-star-catch__eyebrow";
    eyebrow.textContent = detail.first === false ? "星河三角洲 · 再次相遇" : "星河三角洲 · 图鉴新发现";
    var title = document.createElement("h2");
    title.id = "fallen-star-catch-title";
    title.textContent = "落星";
    var latin = document.createElement("p");
    latin.className = "fallen-star-catch__latin";
    latin.textContent = "Stella delapsa";
    var line = document.createElement("p");
    line.className = "fallen-star-catch__line";
    line.textContent = "今夜，银河咬钩了。";
    var description = document.createElement("p");
    description.className = "fallen-star-catch__description";
    description.textContent = "入手不重，微烫。它在你掌心一明一灭，像握住一句没说出口的话。";
    card.appendChild(eyebrow);
    card.appendChild(title);
    card.appendChild(latin);
    card.appendChild(line);
    card.appendChild(description);

    var controls = document.createElement("div");
    controls.className = "fallen-star-catch__controls";
    var sound = document.createElement("button");
    sound.type = "button";
    sound.className = "fallen-star-catch__sound";
    function renderSoundState() {
      sound.textContent = fallenStarMuted() ? "♫ 音效关" : "♫ 音效开";
      sound.setAttribute("aria-pressed", fallenStarMuted() ? "false" : "true");
    }
    renderSoundState();
    sound.addEventListener("click", function () {
      var nextMuted = !fallenStarMuted();
      setFallenStarMuted(nextMuted);
      renderSoundState();
      if (!nextMuted) playFallenStarSound();
    });
    controls.appendChild(sound);

    if (detail.preview) {
      var replay = document.createElement("button");
      replay.type = "button";
      replay.className = "fallen-star-catch__replay";
      replay.textContent = "重播星光";
      replay.addEventListener("click", function () {
        root.remove();
        window.setTimeout(function () { showFallenStar({ preview: true, first: true }); }, 80);
      });
      controls.appendChild(replay);
    }

    var take = document.createElement("button");
    take.type = "button";
    take.className = "fallen-star-catch__take";
    take.textContent = detail.preview ? "收起预览" : "收进图鉴";
    take.addEventListener("click", closeFallenStar);
    controls.appendChild(take);

    card.appendChild(controls);
    root.appendChild(veil);
    root.appendChild(sky);
    root.appendChild(beam);
    root.appendChild(wake);
    root.appendChild(relic);
    root.appendChild(card);
    document.body.appendChild(root);
    playFallenStarSound();
    take.focus({ preventScroll: true });

    root.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeFallenStar();
    });
    if (!detail.preview) {
      fallenStarTimer = window.setTimeout(closeFallenStar, 9200);
    }
  }

  function isFallenStarCatch(data) {
    var result = data && data.result;
    return Boolean(data && data.ok && result && result.kind === "fish" &&
      (result.fish_id === "fallen_star" || result.fish === "落星"));
  }

  function fallenStarPlaybackKey(data) {
    var result = data.result || {};
    return [result.fish_id || result.fish, result.text || "", result.first ? "1" : "0"].join("|");
  }

  window.addEventListener("rainholm:fallen-star", function (event) {
    showFallenStar((event && event.detail) || {});
  });

  function isFallenStarPreview() {
    try {
      var url = new URL(window.location.href);
      var local = url.hostname === "127.0.0.1" || url.hostname === "localhost";
      return local && url.searchParams.get("preview") === "fallen-star";
    } catch (_) {
      return false;
    }
  }

  function fallenStarPreviewFetchArgs(args) {
    var input = args[0];
    var url = typeof input === "string" ? input : input && input.url;
    if (!url || !/\/api\/pond\/cast(?:\?|$)/.test(url) || !isFallenStarPreview()) {
      return args;
    }
    if (typeof input === "string") {
      var options = Object.assign({}, args[1] || {});
      var headers = new Headers(options.headers || {});
      headers.set("X-Rainholm-Preview", "fallen-star");
      options.headers = headers;
      args[1] = options;
    } else if (input) {
      var requestHeaders = new Headers(input.headers || {});
      requestHeaders.set("X-Rainholm-Preview", "fallen-star");
      args[0] = new Request(input, { headers: requestHeaders });
    }
    return args;
  }

  window.fetch = async function () {
    var fetchArgs = fallenStarPreviewFetchArgs(Array.prototype.slice.call(arguments));
    var response = await originalFetch.apply(null, fetchArgs);
    try {
      var input = fetchArgs[0];
      var url = typeof input === "string" ? input : input && input.url;
      if (url && /\/api\/pond\/cast(?:\?|$)/.test(url)) {
        response.clone().json().then(function (data) {
          if (data && data.ok && data.newly_unlocked && data.newly_unlocked.length) {
            showUnlock(data.newly_unlocked);
          }
          if (isFallenStarCatch(data)) {
            var playbackKey = fallenStarPlaybackKey(data);
            if (!fallenStarPlaybackKeys.has(playbackKey)) {
              fallenStarPlaybackKeys.add(playbackKey);
              showFallenStar({ first: data.result.first });
            }
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
  var starryDeltaSpot = /\/assets\/spot_starry_delta\.jpg(?:\?|$)/;

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
    ensureStarryDeltaMeteors(mapLayer, mapImage, mapSource);
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

  function ensureStarryDeltaMeteors(mapLayer, mapImage, mapSource) {
    var isStarryDelta = starryDeltaSpot.test(mapSource);
    var oldShower = mapLayer.querySelector(".rainholm-meteor-shower");
    mapLayer.classList.toggle("rainholm-starry-delta", isStarryDelta);
    if (!isStarryDelta) {
      if (oldShower) oldShower.remove();
      return;
    }
    if (oldShower) return;

    var shower = document.createElement("div");
    shower.className = "rainholm-meteor-shower";
    shower.setAttribute("aria-hidden", "true");
    [
      ["76%", "3%", "8.6s", "-1.4s", ".72", "43vw", "10vh"],
      ["101%", "9%", "10.4s", "-6.8s", "1", "55vw", "12vh"],
      ["63%", "1%", "11.8s", "-9.5s", ".58", "36vw", "8vh"],
      ["90%", "13%", "9.7s", "-4.1s", ".82", "48vw", "10vh"]
    ].forEach(function (settings) {
      var meteor = document.createElement("span");
      meteor.className = "rainholm-meteor";
      meteor.style.setProperty("--meteor-x", settings[0]);
      meteor.style.setProperty("--meteor-y", settings[1]);
      meteor.style.setProperty("--meteor-duration", settings[2]);
      meteor.style.setProperty("--meteor-delay", settings[3]);
      meteor.style.setProperty("--meteor-scale", settings[4]);
      meteor.style.setProperty("--meteor-travel-x", settings[5]);
      meteor.style.setProperty("--meteor-travel-y", settings[6]);
      shower.appendChild(meteor);
    });
    mapImage.insertAdjacentElement("afterend", shower);
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

  function installFallenStarPreview() {
    if (!isFallenStarPreview()) return;
    window.setTimeout(function () {
      showRiverGodToast("落星已入水……测试期间每一竿都会真正写入图鉴。");
    }, 700);
  }

  if (document.readyState === "complete") {
    installFallenStarPreview();
  } else {
    window.addEventListener("load", installFallenStarPreview, { once: true });
  }

  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest && event.target.closest("button");
    if (!button || button !== jadePlusButton()) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    openRiverGodFromJadePlus(button);
  }, true);
})();
