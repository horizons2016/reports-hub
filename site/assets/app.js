/* مستودع البحوث والتقارير — dashboard. Reads data/*.json written by the collector. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const TZ = "Asia/Muscat";
  const LOC = "ar-OM-u-nu-latn";
  const fmtDate = new Intl.DateTimeFormat(LOC, { timeZone: TZ, day: "numeric", month: "short", year: "numeric" });
  const fmtDT = new Intl.DateTimeFormat(LOC, { timeZone: TZ, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  const fmtMonth = new Intl.DateTimeFormat(LOC, { timeZone: TZ, month: "short" });
  const nf = new Intl.NumberFormat("en-US");
  const DAY = 86400000;

  const FREQ = { monthly: "شهري", quarterly: "ربعي", annual: "سنوي", weekly: "أسبوعي", other: "أخرى" };
  const SLOT = { pdf_ar: "PDF ع", pdf_en: "PDF En", xls_ar: "Excel ع", xls_en: "Excel En", all: "كل الملفات" };
  const SLOT_LONG = { pdf_ar: "PDF عربي", pdf_en: "PDF إنجليزي", xls_ar: "Excel عربي", xls_en: "Excel إنجليزي", all: "كل الملفات" };
  const EV = {
    new: ["إصدار جديد", "new", "أول ظهور للإصدار في قائمة المركز."],
    revision: ["تعديل", "rev", "رُفع ملف جديد لإصدار قائم، ومحتواه يختلف عن النسخة السابقة."],
    revision_same_url: ["تعديل على نفس الرابط", "rev", "تغيّر محتوى الملف دون أن يتغيّر رابطه (يُكشف بإعادة الفحص خلال فترة المراجعة)."],
    new_upload_unverified: ["رفع جديد غير مُتحقق", "rev", "رُفع ملف جديد ولم يمكن مقارنة محتواه بالسابق."],
    file_added: ["ملف أُضيف", "add", "ظهر ملف لم يكن موجوداً، غالباً ملف Excel بعد أيام من PDF."],
    reupload: ["إعادة رفع", "", "رُفع الملف من جديد بنفس المحتوى تماماً. لا يستدعي إجراء."],
    late_older: ["نسخة أقدم", "", "ظهر رابط أقدم من النسخة الحالية."],
    nsdp_new_period: ["SDMX: فترة جديدة", "sdmx", "أُضيفت فترة زمنية جديدة إلى ملف SDMX."],
    nsdp_update: ["SDMX: تحديث", "sdmx", "تغيّر ملف SDMX دون إضافة فترة جديدة (قد يكون تنقيحاً لقيم سابقة)."],
    baseline: ["أول رصد", "", "النسخة الموجودة يوم بدء الرصد، مسجّلة كنسخة أولى."],
  };
  const REV_TYPES = new Set(["revision", "revision_same_url", "new_upload_unverified"]);

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const d = (s) => (s ? new Date(s) : null);
  const dateTxt = (s) => (s ? fmtDate.format(new Date(s)) : "—");
  const dtTxt = (s) => (s ? fmtDT.format(new Date(s)) : "—");
  const safeUrl = (u) => (/^https:\/\//.test(u || "") ? u : "#");
  const title = (p) => p.title_ar || p.title_en || p.id;
  const chip = (txt, cls = "") => `<span class="chip ${cls}">${esc(txt)}</span>`;
  const evChip = (t) => chip((EV[t] || [t])[0], (EV[t] || [])[1] || "");
  const per = (s) => `<bdi dir="ltr">${esc(s || "—")}</bdi>`;
  const fromTo = (dt) => `من ${per(dt.from)} إلى ${per(dt.to)}`;
  const median =(a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  const state = { pubs: [], events: [], nsdp: [], status: {}, settings: {}, indicators: { indicators: [] }, tables: { tables: [] } };

  async function getJSON(path, fallback) {
    try { const r = await fetch(path, { cache: "no-store" }); if (!r.ok) throw 0; return await r.json(); }
    catch { return fallback; }
  }

  async function load() {
    const [p, e, n, s, cfg, ind, bt] = await Promise.all([
      getJSON("data/publications.json", { publications: [] }),
      getJSON("data/events.json", { events: [] }),
      getJSON("data/nsdp.json", { categories: [] }),
      getJSON("data/status.json", {}),
      getJSON("data/settings.json", {}),
      getJSON("data/indicators.json", { indicators: [] }),
      getJSON("data/bulletin_tables.json", { tables: [] }),
    ]);
    state.pubs = p.publications || [];
    state.events = (e.events || []).slice().reverse();
    state.nsdp = n.categories || [];
    state.status = s; state.settings = cfg; state.indicators = ind; state.tables = bt;
    state.pubById = Object.fromEntries(state.pubs.map((x) => [x.id, x]));
  }

  /* ---------------- header & banner ---------------- */
  function renderStatus() {
    const s = state.status, el = $("#run-status");
    const repo = s.repository || guessRepo();
    $("#repo-link").href = `https://github.com/${repo}`;
    if (!s.last_run) { el.className = "run warn"; el.innerHTML = `<span class="dot"></span><span>لم يكتمل أول تشغيل بعد</span>`; return; }
    const ok = s.harvest_ok && s.nsdp_ok > 0;
    el.className = "run " + (ok ? "ok" : s.harvest_ok || s.nsdp_ok ? "warn" : "bad");
    el.innerHTML = `<span class="dot"></span><span>آخر تحديث: ${esc(dtTxt(s.finished || s.last_run))}</span>`;
    const b = $("#banner");
    if (!s.harvest_ok) {
      b.innerHTML = `<div class="banner warn"><b>تعذّرت قراءة قائمة الإصدارات في آخر تشغيل.</b> تُعرض آخر بيانات محفوظة. تفاصيل التشغيل في <a href="https://github.com/${esc(repo)}/actions">سجل التشغيل</a>.</div>`;
    } else if (s.baseline_run) {
      b.innerHTML = `<div class="banner info"><b>بدأ الرصد ${esc(dateTxt(s.monitoring_since))}.</b> كل إصدار سُجّل الآن كنسخة أولى (v1). التعديلات اللاحقة تُرصد من هذا التاريخ، ويظهر التاريخ السابق في «استُبدل بعد النشر».</div>`;
    }
  }
  function guessRepo() {
    const h = location.hostname.split(".")[0], p = location.pathname.split("/").filter(Boolean)[0];
    return location.hostname.endsWith("github.io") && p ? `${h}/${p}` : "horizons2016/reports-hub";
  }

  /* ---------------- overview ---------------- */
  function renderOverview() {
    const now = Date.now();
    const pubs = state.pubs;
    const new7 = pubs.filter((p) => d(p.released_at) && now - d(p.released_at) <= 7 * DAY).length;
    const rev30 = state.events.filter((e) => REV_TYPES.has(e.type) && now - d(e.at) <= 30 * DAY).length;
    const prov = pubs.filter((p) => p.provisional);
    const eligible = pubs.filter((p) => !p.migrated && !p.legacy);
    const replaced = eligible.filter((p) => p.replaced_after_release).length;
    const fresh = state.nsdp.filter((c) => c.freshness === "fresh").length;
    const provDays = state.settings.provisional_days || 15;
    const k = [
      ["إجمالي الإصدارات المرصودة", nf.format(pubs.length), state.status.monitoring_since ? `منذ ${dateTxt(state.status.monitoring_since)}` : ""],
      ["جديد خلال 7 أيام", nf.format(new7), "حسب تاريخ النشر"],
      ["تعديلات خلال 30 يوماً", nf.format(rev30), "محتوى تغيّر بعد النشر", rev30 > 0],
      [`قيد المراجعة (${provDays} يوماً)`, nf.format(prov.length), "أرقامها قد تُعدَّل", prov.length > 0],
      ["استُبدل ملفها بعد النشر", eligible.length ? `${Math.round((100 * replaced) / eligible.length)}%` : "—", `${nf.format(replaced)} من ${nf.format(eligible.length)}، حدّ أدنى تاريخي`],
      ["فئات SDMX حديثة", state.nsdp.length ? `${fresh}/${state.nsdp.length}` : "—", "صفحة البيانات الموجزة"],
    ];
    $("#kpis").innerHTML = k.map(([lab, val, note, hl]) =>
      `<div class="kpi${hl ? " hl" : ""}"><div class="lab">${esc(lab)}</div><div class="val">${esc(val)}</div><div class="note">${esc(note)}</div></div>`).join("");

    const evs = state.events.filter((e) => e.type !== "reupload" && e.type !== "late_older").slice(0, 8);
    $("#ov-events").innerHTML = evs.length ? evs.map(eventLi).join("") : `<li class="empty">لا تنبيهات بعد. ستظهر هنا عند أول إصدار جديد أو تعديل.</li>`;

    $("#prov-hint").textContent = `ما نُشر خلال آخر ${provDays} يوماً`;
    prov.sort((a, b) => d(b.released_at) - d(a.released_at));
    $("#ov-provisional").innerHTML = prov.length ? prov.slice(0, 8).map((p) => {
      const left = Math.max(0, Math.ceil(provDays - (now - d(p.released_at)) / DAY));
      return `<li><div class="t"><b>${esc(title(p))}</b><div class="meta">نُشر ${esc(dateTxt(p.released_at))} · ${p.version_count > 1 ? `${p.version_count} نسخ` : "نسخة واحدة"}</div></div>${chip(`${left} يوماً متبقية`, "prov")}</li>`;
    }).join("") : `<li class="empty">لا إصدارات في فترة المراجعة.</li>`;

    monthsChart();
  }

  function eventLi(e) {
    const name = e.title_ar || e.title_en || e.id;
    const slot = e.slot && e.slot !== "all" ? ` · ${SLOT_LONG[e.slot] || e.slot}` : "";
    const det = e.detail ? ` · ${fromTo(e.detail)}` : "";
    const link = e.issue_url || e.url;
    return `<li>${evChip(e.type)}<div class="t"><b>${link ? `<a href="${esc(safeUrl(link))}" rel="noopener">${esc(name)}</a>` : esc(name)}</b><div class="meta">${esc(dtTxt(e.at))}${esc(slot)}${det}</div></div></li>`;
  }

  function monthsChart() {
    const el = $("#ov-months");
    const now = new Date();
    const months = [];
    for (let i = 23; i >= 0; i--) {
      const m = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
      months.push({ key: m.toISOString().slice(0, 7), date: m, n: 0, r: 0 });
    }
    const idx = Object.fromEntries(months.map((m, i) => [m.key, i]));
    for (const p of state.pubs) {
      const k = (p.released_at || "").slice(0, 7);
      if (k in idx) { months[idx[k]].n++; if (p.replaced_after_release || p.version_count > 1) months[idx[k]].r++; }
    }
    const W = Math.max(320, el.clientWidth || 800), H = 220, padL = 10, padR = 34, padT = 14, padB = 30;
    const max = Math.max(1, ...months.map((m) => m.n));
    const step = Math.ceil(max / 4) || 1, top = step * 4;
    const bw = (W - padL - padR) / months.length;
    const y = (v) => padT + (H - padT - padB) * (1 - v / top);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="الإصدارات شهرياً">`;
    for (let g = 0; g <= 4; g++) {
      const v = g * step, yy = y(v);
      s += `<line x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}" stroke="var(--line)"/><text x="${W - padR + 6}" y="${yy + 4}" text-anchor="start">${v}</text>`;
    }
    months.forEach((m, i) => {
      // RTL: newest month on the left
      const x = W - padR - (i + 1) * bw + bw * 0.18, w = bw * 0.64;
      const h = y(0) - y(m.n), hr = y(0) - y(m.r);
      s += `<g class="mb" data-i="${i}"><rect x="${x - bw * 0.18}" y="${padT}" width="${bw}" height="${H - padT - padB}" fill="transparent"/>`;
      if (m.n) s += `<rect x="${x}" y="${y(m.n)}" width="${w}" height="${h}" rx="3" fill="var(--bar)"/>`;
      if (m.r) s += `<rect x="${x}" y="${y(m.r)}" width="${w}" height="${hr}" rx="3" fill="var(--bar-strong)"/>`;
      s += `</g>`;
      const every = W < 600 ? 6 : 3;
      if (i % every === 0 || i === months.length - 1) {
        const lab = m.date.getUTCMonth() === 0 || i === 0 || i === months.length - 1 ?`${fmtMonth.format(m.date)} ${m.date.getUTCFullYear()}` : fmtMonth.format(m.date);
        s += `<text x="${x + w / 2}" y="${H - 10}" text-anchor="middle">${esc(lab)}</text>`;
      }
    });
    s += `</svg><div class="tip"></div>`;
    el.innerHTML = s;
    const tip = $(".tip", el);
    el.querySelectorAll(".mb").forEach((g) => {
      g.addEventListener("mousemove", (ev) => {
        const m = months[+g.dataset.i], r = el.getBoundingClientRect();
        tip.innerHTML = `<b>${esc(fmtMonth.format(m.date))} ${m.date.getUTCFullYear()}</b><br>${m.n} إصداراً · ${m.r} استُبدل ملفه`;
        tip.style.display = "block";
        const tx = Math.min(Math.max(ev.clientX - r.left - 70, 0), r.width - 160);
        tip.style.left = tx + "px"; tip.style.top = ev.clientY - r.top - 54 + "px";
      });
      g.addEventListener("mouseleave", () => (tip.style.display = "none"));
    });
  }

  /* ---------------- publications ---------------- */
  let pubLimit = 50;
  function setupPubFilters() {
    const cats = [...new Set(state.pubs.map((p) => p.category_ar || p.category_en).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ar"));
    $("#f-cat").insertAdjacentHTML("beforeend", cats.map((c) => `<option>${esc(c)}</option>`).join(""));
    if (!cats.length) {  // the API gives no category names yet: hide the filter and the column
      $("#f-cat").hidden = true;
      document.body.classList.add("no-cat");
    }
    const years = [...new Set(state.pubs.map((p) => (p.released_at || "").slice(0, 4)).filter(Boolean))].sort().reverse();
    $("#f-year").insertAdjacentHTML("beforeend", years.map((y) => `<option>${y}</option>`).join(""));
    ["#f-q", "#f-cat", "#f-freq", "#f-status", "#f-year"].forEach((id) => $(id).addEventListener("input", () => { pubLimit = 50; renderPubs(); }));
    $("#pub-more").addEventListener("click", () => { pubLimit += 100; renderPubs(); });
  }
  function filteredPubs() {
    const q = $("#f-q").value.trim().toLowerCase(), cat = $("#f-cat").value, fr = $("#f-freq").value, st = $("#f-status").value, yr = $("#f-year").value;
    return state.pubs.filter((p) => {
      if (q && !`${p.title_ar || ""} ${p.title_en || ""} ${p.series || ""}`.toLowerCase().includes(q)) return false;
      if (cat && (p.category_ar || p.category_en) !== cat) return false;
      if (fr && p.frequency !== fr) return false;
      if (yr && !(p.released_at || "").startsWith(yr)) return false;
      if (st === "provisional" && !p.provisional) return false;
      if (st === "versions" && !(p.version_count > 1)) return false;
      if (st === "replaced" && !p.replaced_after_release) return false;
      return true;
    });
  }
  function fileLinks(p) {
    return ["pdf_ar", "pdf_en", "xls_ar", "xls_en"].filter((s) => p.files && p.files[s])
      .map((s) => `<a href="${esc(safeUrl(p.files[s].url))}" rel="noopener" target="_blank">${SLOT[s]}</a>`).join("");
  }
  function statusChips(p) {
    const c = [];
    if (p.provisional) c.push(chip("قيد المراجعة", "prov"));
    if (p.version_count > 1) c.push(chip(`v${p.version_count}`, "ver"));
    if (p.replaced_after_release) c.push(chip("استُبدل بعد النشر", "rev"));
    return c.join(" ") || chip("v1", "ver");
  }
  function renderPubs() {
    const rows = filteredPubs();
    $("#pub-count").textContent = `${nf.format(rows.length)} إصداراً`;
    const body = $("#pub-table tbody");
    if (!state.pubs.length) { body.innerHTML = `<tr><td colspan="6" class="empty">لا بيانات بعد.</td></tr>`; $("#pub-more").hidden = true; return; }
    body.innerHTML = rows.slice(0, pubLimit).map((p) => `
      <tr class="row" data-id="${esc(p.id)}">
        <td><div class="ttl">${esc(p.title_ar || p.title_en)}</div>${p.title_en && p.title_ar ? `<div class="ttl-en">${esc(p.title_en)}</div>` : ""}</td>
        <td class="hide-s small">${esc(p.category_ar || p.category_en || "—")}</td>
        <td class="small">${esc(FREQ[p.frequency] || "—")}</td>
        <td class="num small">${esc(dateTxt(p.released_at))}</td>
        <td>${statusChips(p)}</td>
        <td class="files">${fileLinks(p)}</td>
      </tr>`).join("");
    $("#pub-more").hidden = rows.length <= pubLimit;
    body.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", (ev) => { if (ev.target.closest("a")) return; toggleDetail(tr); }));
  }
  function toggleDetail(tr) {
    const nxt = tr.nextElementSibling;
    if (nxt && nxt.classList.contains("detail")) { nxt.remove(); tr.classList.remove("open"); return; }
    const p = state.pubById[tr.dataset.id];
    tr.classList.add("open");
    const slots = Object.entries(p.versions || {}).sort();
    const vs = slots.map(([slot, list]) => `<div class="vslot"><h4>${esc(SLOT_LONG[slot] || slot)}</h4><ol>${list.map((v) => `
        <li><b>v${v.v}</b> ${evChip(v.status)}
        <div class="when">رُفع ${esc(dtTxt(v.uploaded))}${v.size ? ` · ${(v.size / 1048576).toFixed(1)} م.ب` : ""}${v.sha256 ? ` · بصمة ${esc(v.sha256.slice(0, 10))}` : ""}</div>
        <div class="when"><a href="${esc(safeUrl(v.url))}" target="_blank" rel="noopener">الرابط لدى المركز</a>${v.archive_url ? ` · <a href="${esc(safeUrl(v.archive_url))}">نسخة مؤرشفة</a>` : ""}</div></li>`).join("")}</ol></div>`).join("");
    const delay = p.current_file_delay_days;
    const hist = p.replaced_after_release ? `<p class="small">الملف الحالي رُفع بعد ${esc(delay)} يوماً من النشر، أي أن الملف الأصلي استُبدل مرة واحدة على الأقل.</p>`
      : p.migrated ? `<p class="small muted">إصدار من الأرشيف أُعيد رفع ملفاته عند نقل موقع المركز (سبتمبر–أكتوبر 2025).</p>`
      : p.legacy ? `<p class="small muted">ملفات من نظام المركز السابق، لا تحمل وقت رفع. أي تعديل عليها من الآن يُرصد بالبصمة.</p>` : "";
    tr.insertAdjacentHTML("afterend", `<tr class="detail"><td colspan="6">${hist}<div class="vers">${vs}</div>
      <p class="small muted">السلسلة: ${esc(p.series || "—")} · رقم الإصدار لدى المركز: ${esc(p.publication_id ?? "—")} · أول رصد: ${esc(dateTxt(p.first_seen))}</p></td></tr>`);
  }

  /* ---------------- revisions ---------------- */
  function renderRevisions() {
    $("#since-hint").textContent = state.status.monitoring_since ? `منذ ${dateTxt(state.status.monitoring_since)}` : "";
    const revs = state.events.filter((e) => REV_TYPES.has(e.type) || e.type === "reupload");
    $("#rev-live").innerHTML = revs.length ? `<table class="tbl"><thead><tr><th>الوقت</th><th>النوع</th><th>الإصدار</th><th class="hide-s">الملف</th><th>النسختان</th></tr></thead><tbody>${revs.map((e) => `
      <tr><td class="num small">${esc(dtTxt(e.at))}</td><td>${evChip(e.type)}</td><td>${esc(e.title_ar || e.title_en)}</td><td class="hide-s small">${esc(SLOT_LONG[e.slot] || e.slot || "")}</td>
      <td class="small">${e.previous ? `من <a href="${esc(safeUrl(e.previous.archive_url || e.previous.url))}">v${e.previous.v}</a> إلى ` : ""}<a href="${esc(safeUrl(e.url))}">v${esc(e.version)}</a></td></tr>`).join("")}</tbody></table>`
      : `<p class="empty">لم يُرصد أي تعديل منذ بدء الرصد. عند حدوثه يظهر هنا مع رابطي النسختين.</p>`;

    // historical delays
    const eligible = state.pubs.filter((p) => !p.migrated && !p.legacy);
    const migrated = state.pubs.length - eligible.length;
    const delays = eligible.filter((p) => p.replaced_after_release).map((p) => p.current_file_delay_days);
    const buckets = [["أقل من يوم", 0, 1], ["1–3 أيام", 1, 4], ["4–7 أيام", 4, 8], ["8–15 يوماً", 8, 16], ["16–30 يوماً", 16, 31], ["أكثر من 30", 31, 1e9]];
    const counts = buckets.map(([, a, b]) => delays.filter((x) => x >= a && x < b).length);
    hbar($("#rev-delays"), buckets.map((b, i) => ({ label: b[0], value: counts[i], strong: i >= 1 && i <= 3 })), "إصداراً");
    const within15 = delays.filter((x) => x < 16).length;
    $("#rev-note").innerHTML = state.pubs.length ? `
      <p>من ${nf.format(eligible.length)} إصداراً، استُبدل ملف <b>${nf.format(delays.length)}</b> بعد نشره مرة واحدة على الأقل${delays.length ? `، و${nf.format(within15)} منها خلال 15 يوماً` : ""}.</p>
      ${migrated ? `<p>استُبعد ${nf.format(migrated)} إصداراً من الأرشيف: ملفاتها إما أُعيد رفعها دفعةً واحدة عند نقل موقع المركز (سبتمبر–أكتوبر 2025)، أو من النظام السابق ولا تحمل وقت رفع. لا يمكن الحكم على استبدالها تاريخياً، لكن أي تعديل عليها من الآن يُرصد.</p>` : ""}
      <p>الطريقة: نقارن وقت رفع الملف الحالي (مكتوب في اسمه) بوقت إنشاء سجل الإصدار لدى المركز. الفرق الأكبر من ربع ساعة يعني أن الملف الأصلي استُبدل.</p>
      <p>هذا حدّ أدنى: المركز يُبقي الملف الأخير فقط، فلا نعرف عدد مرات الاستبدال قبل بدء الرصد. منذ بدء الرصد تُعدّ كل نسخة.</p>` : `<p class="empty">تظهر القراءة بعد أول تشغيل.</p>`;

    // by series
    const by = {};
    for (const p of eligible) {
      const k = p.series || title(p);
      (by[k] ||= { n: 0, r: 0, delays: [], ar: p.title_ar ? p.title_ar.split(/[:：]/)[0].trim() : "" }).n++;
      if (p.replaced_after_release) { by[k].r++; by[k].delays.push(p.current_file_delay_days); }
    }
    const rows = Object.entries(by).filter(([, v]) => v.n >= 3).sort((a, b) => b[1].r / b[1].n - a[1].r / a[1].n || b[1].n - a[1].n).slice(0, 40);
    $("#series-table tbody").innerHTML = rows.length ? rows.map(([k, v]) => `
      <tr><td><div class="ttl">${esc(v.ar || k)}</div>${v.ar ? `<div class="ttl-en">${esc(k)}</div>` : ""}</td><td class="num">${v.n}</td><td class="num">${v.r}</td>
      <td class="num"><b>${Math.round((100 * v.r) / v.n)}%</b></td><td class="num hide-s">${v.delays.length ? `${median(v.delays).toFixed(1)} يوماً` : "—"}</td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">تظهر بعد أول تشغيل (السلاسل التي لها 3 إصدارات فأكثر).</td></tr>`;
  }

  function hbar(el, items, unit) {
    const W = Math.max(300, el.clientWidth || 500), rowH = 30, H = items.length * rowH + 10, lab = 96, padR = 8;
    const max = Math.max(1, ...items.map((i) => i.value));
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="توزيع التأخر">`;
    items.forEach((it, i) => {
      const y = i * rowH + 6, w = ((W - lab - padR - 40) * it.value) / max;
      s += `<text x="${W - padR}" y="${y + 15}" text-anchor="start" style="direction:rtl">${esc(it.label)}</text>`;
      s += `<rect x="${W - lab - w}" y="${y + 2}" width="${Math.max(w, it.value ? 2 : 0)}" height="18" rx="4" fill="${it.strong ? "var(--bar-strong)" : "var(--bar)"}"><title>${esc(it.label)}: ${it.value} ${unit}</title></rect>`;
      s += `<text x="${W - lab - w - 6}" y="${y + 15}" text-anchor="end">${it.value}</text>`;
    });
    el.innerHTML = s + "</svg>";
    el.style.minHeight = "0";
  }

  /* ---------------- SDMX ---------------- */
  function renderSdmx() {
    const cats = state.nsdp;
    const c = { fresh: 0, stale: 0, unavailable: 0 };
    cats.forEach((x) => (c[x.freshness] = (c[x.freshness] || 0) + 1));
    $("#sdmx-sum").innerHTML = cats.length ? `${chip(`${c.fresh} حديثة`, "ok")} ${chip(`${c.stale} متأخرة`, "stale")} ${chip(`${c.unavailable} غير متاحة`, "na")}` : `<span class="empty">تظهر بعد أول تشغيل.</span>`;
    const order = { fresh: 0, stale: 1, unavailable: 2 };
    const FQ = { M: "شهري", Q: "ربعي", A: "سنوي" };
    $("#sdmx-grid").innerHTML = cats.slice().sort((a, b) => order[a.freshness] - order[b.freshness]).map((x) => `
      <div class="sd">
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><h3>${esc(x.ar)}</h3>${x.freshness === "fresh" ? chip("حديثة", "ok") : x.freshness === "stale" ? chip("متأخرة", "stale") : chip("غير متاحة", "na")}</div>
        <div class="en">${esc(x.en)}</div>
        <div class="big">${esc(x.latest_period || "—")}</div>
        <div class="row"><span>آخر فترة · ${esc(FQ[x.main_frequency] || x.main_frequency || "—")}</span><span>${x.lag_months != null ? `تأخر ${x.lag_months} شهراً` : ""}</span></div>
        <div class="row"><span>${x.series ? `${nf.format(x.series)} سلسلة · ${nf.format(x.observations)} مشاهدة` : ""}</span><span>${x.last_changed ? `تغيّر ${esc(dateTxt(x.last_changed))}` : ""}</span></div>
        <div class="links">${x.url ? `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener">SDMX</a>` : ""}<a href="${esc(safeUrl(x.excel_url))}" target="_blank" rel="noopener">Excel</a></div>
      </div>`).join("");
  }

  /* ---------------- alerts ---------------- */
  function renderAlerts() {
    const repo = state.status.repository || guessRepo();
    const feed = new URL("data/feed.xml", location.href).href;
    $("#subscribe").innerHTML = `
      <li>ادخل <a href="https://github.com/${esc(repo)}">المستودع على GitHub</a> بحسابك.</li>
      <li>اضغط <b>Watch</b> ثم <b>Custom</b> وفعّل <b>Issues</b>. يصلك كل تنبيه على بريدك.</li>
      <li>أو تابع <a href="https://github.com/${esc(repo)}/issues">صفحة التنبيهات</a> مباشرة، أو اشترك في <a href="${esc(feed)}">خلاصة RSS</a>.</li>
      <li>يعمل الرصد تلقائياً مرتين يومياً (نحو 6 صباحاً و2 ظهراً بتوقيت مسقط).</li>`;
    $("#legend").innerHTML = Object.entries(EV).map(([k, v]) => `<li>${chip(v[0], v[1])}<span class="small">${esc(v[2])}</span></li>`).join("");
    const types = [...new Set(state.events.map((e) => e.type))];
    const sel = $("#f-type");
    sel.insertAdjacentHTML("beforeend", types.map((t) => `<option value="${esc(t)}">${esc((EV[t] || [t])[0])}</option>`).join(""));
    const draw = () => {
      const t = sel.value;
      const rows = state.events.filter((e) => !t || e.type === t).slice(0, 300);
      $("#ev-table tbody").innerHTML = rows.length ? rows.map((e) => `
        <tr><td class="num small">${esc(dtTxt(e.at))}</td><td>${evChip(e.type)}</td><td>${esc(e.title_ar || e.title_en || e.id)}${e.detail ? `<div class="small muted">${fromTo(e.detail)}</div>` : ""}</td>
        <td class="hide-s small">${esc(SLOT_LONG[e.slot] || e.slot || "")}</td>
        <td class="small">${e.issue_url ? `<a href="${esc(safeUrl(e.issue_url))}">التنبيه</a> · ` : ""}${e.url ? `<a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">الملف</a>` : ""}</td></tr>`).join("")
        : `<tr><td colspan="5" class="empty">لا تنبيهات بعد.</td></tr>`;
    };
    sel.addEventListener("input", draw); draw();
  }

  /* ---------------- indicators ---------------- */
  function renderIndicators() {
    const list = state.indicators.indicators || [];
    $("#ind-intro").innerHTML = `<p>هنا يُربط كل مؤشر من مؤشرات الرؤية بمصدره لدى المركز: أي إصدار أو جدول أو سلسلة SDMX يغذّيه، وما نحتاجه من المركز إن لم يكن متاحاً. بعد الربط، أي تعديل في المصدر يظهر على المؤشر المتأثر مباشرة.</p>
      <p class="small muted">تُضاف المؤشرات في الملف <code>config/indicators.json</code> بعد اعتماد منهجياتها.</p>`;
    $("#ind-list").innerHTML = list.length ? `<table class="tbl"><thead><tr><th>المؤشر</th><th>الأولوية</th><th>المصدر</th><th>آخر تحديث للمصدر</th></tr></thead><tbody>${list.map((i) => `
      <tr><td><b>${esc(i.name_ar)}</b><div class="small muted">${esc(i.methodology || "")}</div></td><td class="small">${esc(i.priority || "")}</td>
      <td class="small">${(i.sources || []).map((s) => esc(s.label || s.ref || s.type)).join("<br>")}</td><td class="small">${esc(sourceStatus(i.sources))}</td></tr>`).join("")}</tbody></table>`
      : `<p class="empty">لم تُضف مؤشرات بعد.</p>`;
    const nsdpBy = Object.fromEntries(state.nsdp.map((c) => [c.key, c]));
    const bulletin = state.pubs.find((p) => /monthly statistical bulletin/i.test(p.title_en || ""));
    $("#bt-table tbody").innerHTML = (state.tables.tables || []).map((t) => {
      let src, st;
      if (t.best_source.startsWith("nsdp:")) {
        const c = nsdpBy[t.best_source.slice(5)];
        src = `SDMX: ${c ? c.ar : t.best_source.slice(5)}`;
        st = c ? (c.freshness === "fresh" ? chip(c.latest_period, "ok") : c.freshness === "stale" ? chip(c.latest_period || "متأخر", "stale") : chip("غير متاح", "na")) : "—";
      } else {
        src = "Excel النشرة الشهرية";
        st = bulletin ? chip(bulletin.files && bulletin.files.xls_en ? "Excel متاح" : "PDF فقط", bulletin.files && bulletin.files.xls_en ? "ok" : "prov") : "—";
      }
      return `<tr><td class="num">${esc(t.table)}</td><td>${esc(t.title_ar)}</td><td class="small">${esc(t.frequency)}</td><td class="small">${esc(t.axis)}</td><td class="small">${esc(src)}</td><td class="hide-s">${st}</td></tr>`;
    }).join("");
  }
  function sourceStatus(sources) {
    for (const s of sources || []) {
      if (s.type === "nsdp") { const c = state.nsdp.find((x) => x.key === s.ref); if (c) return c.latest_period || "—"; }
    }
    return "—";
  }

  /* ---------------- method ---------------- */
  function renderMethod() {
    const prov = state.settings.provisional_days || 15;
    $("#method").innerHTML = `
      <h2>لماذا هذا المستودع</h2>
      <p>يستبدل المركز أحياناً ملفات إصدارات منشورة بنسخ معدّلة، دون أن يكتب رقم نسخة على الغلاف. من نزّل الملف يوم النشر قد يعمل بأرقام تغيّرت. هنا نرقّم النسخ بأنفسنا ونُنبّه عند كل تعديل.</p>
      <h2>المصادر</h2>
      <ul>
        <li><b>قائمة إصدارات المركز:</b> تُقرأ من واجهة البيانات التي يستخدمها موقع المركز نفسه، وفيها لكل إصدار: العنوان والفئة وتاريخ النشر وروابط ملفات PDF وExcel بالعربية والإنجليزية.</li>
        <li><b>صفحة البيانات الوطنية الموجزة (NSDP):</b> 20 ملفاً بصيغة SDMX تنشرها المركز وفق معيار صندوق النقد الدولي e-GDDS.</li>
      </ul>
      <h2>كيف نكشف النسخ</h2>
      <ol>
        <li><b>الرابط:</b> اسم كل ملف لدى المركز ينتهي بلحظة رفعه (مثل <code>…_1792489912.pdf</code>). رابط جديد لنفس الإصدار يعني رفعاً جديداً.</li>
        <li><b>البصمة:</b> نحسب بصمة المحتوى (SHA-256) لكل ملف جديد. بصمة مطابقة للنسخة السابقة تعني <i>إعادة رفع</i>؛ بصمة مختلفة تعني <i>تعديلاً</i> ويصدر تنبيه.</li>
        <li><b>إعادة الفحص:</b> خلال أول ${prov} يوماً من النشر نعيد حساب بصمة الملف الحالي في كل تشغيل، فنكشف أيضاً الاستبدال على نفس الرابط.</li>
        <li><b>الأرشفة:</b> كل نسخة جديدة تُحفظ نسخة منها في إصدارات المستودع (Releases)، فتبقى النسخة الأصلية متاحة للمقارنة بعد استبدالها.</li>
      </ol>
      <h2>فترة المراجعة</h2>
      <p>تحليل إصدارات أبريل–سبتمبر 2026 أظهر أن كل أعداد النشرة الشهرية القابلة للفحص استُبدلت بعد نشرها، غالباً خلال 4 إلى 14 يوماً. لذلك يُعامَل كل إصدار كـ<b>قيد المراجعة</b> خلال أول ${prov} يوماً، وتُوسم أرقامه بأنها أولية.</p>
      <h2>ما قبل بدء الرصد</h2>
      <p>المركز يُبقي الملف الأخير فقط لكل إصدار. لذلك نستنتج الاستبدال التاريخي من الفرق بين وقت رفع الملف الحالي ووقت إنشاء سجل الإصدار (أكثر من ربع ساعة). هذا يثبت أن الملف استُبدل <i>مرة واحدة على الأقل</i>، ولا يكشف عدد المرات.</p>
      <p>يُستثنى من ذلك أرشيف المركز القديم: عند نقل الموقع (سبتمبر–أكتوبر 2025) أُعيد رفع ملفات الإصدارات القديمة دفعةً واحدة بأسماء من نوع <code>publicationpdf…</code>. هذه نقل للأرشيف وليست تعديلات، فلا تُحسب في نسب الاستبدال.</p>
      <h2>حداثة بيانات SDMX</h2>
      <p>الفئة حديثة إذا لم تتأخر آخر فترة فيها عن اليوم أكثر من 4 أشهر للبيانات الشهرية، و7 للربعية، و18 للسنوية، وهي فترات النشر المعتادة لهذه البيانات. إن كان في الملف أكثر من دورية، تُحسب الأحدث نسبةً إلى فترتها.</p>
      <h2>حدود</h2>
      <ul>
        <li>البصمة تقول إن المحتوى تغيّر، ولا تقول أي رقم تغيّر. مقارنة الجداول خلية بخلية مرحلة لاحقة.</li>
        <li>إذا غيّر المركز طريقة نشر قائمته، قد يتعذّر الحصاد مؤقتاً، ويظهر ذلك في أعلى الصفحة.</li>
      </ul>`;
  }

  /* ---------------- tabs ---------------- */
  const rendered = {};
  const R = { overview: renderOverview, publications: () => { setupPubFilters(); renderPubs(); }, revisions: renderRevisions, sdmx: renderSdmx, alerts: renderAlerts, indicators: renderIndicators, method: renderMethod };
  function show() {
    const t = (location.hash || "#overview").slice(1);
    const tab = R[t] ? t : "overview";
    document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
    if (!rendered[tab]) { rendered[tab] = true; R[tab](); }
  }
  window.addEventListener("hashchange", show);
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (rendered.overview) monthsChart(); if (rendered.revisions) renderRevisions(); }, 200); });

  load().then(() => { renderStatus(); show(); });
})();
