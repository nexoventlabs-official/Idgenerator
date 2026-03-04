import re

with open(r'c:\Users\Admin\Desktop\Politics\voter-id-generator\templates\user\chatbot.html', 'r', encoding='utf-8') as f:
    html = f.read()

new_script = r"""(function () {
      /* ── Localization ── */
      const i18n = {
        en: {
          banner: '<strong>Welcome to PuratchiThaai!</strong><br>Your Digital Member ID Card Generator<br><br>\u{1F44B} Hello! Welcome to <strong>PuratchiThaai</strong> ID Card Generator.',
          start_btn: '<i class="bi bi-play-circle-fill me-1"></i> Start',
          starting: '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span> Starting...',
          ask_mobile: '\u{1F4F1} Please enter your <strong>10-digit mobile number</strong> to verify:',
          invalid_mobile: '\u274C Please enter a valid <strong>10-digit mobile number</strong>.',
          ask_otp: '\u{1F4DE} You will receive an <strong>OTP call</strong> on <strong>+91 {m}</strong>.<br><br>Please enter the <strong>6-digit OTP</strong> you hear on the call:',
          otp_fail: '\u274C Could not send OTP. Please try again.',
          invalid_otp: '\u274C Please enter a valid <strong>6-digit OTP</strong>.',
          otp_verified_exists: '\u2705 Mobile verified! Your <strong>ID Card</strong> was already generated.',
          download_btn: '<i class="bi bi-download"></i> Download Card',
          type_anything: '<em style="color:#667781">Type anything to generate another card.</em>',
          ask_epic: '\u2705 Mobile number verified!<br><br>Please enter your <strong>EPIC Number</strong> (Voter ID):',
          invalid_otp_res: 'Invalid OTP. Please try again.',
          verif_fail: '\u274C Verification failed. Please try again.',
          voter_found: '\u2705 <strong>Voter Found!</strong>',
          upload_photo: '\u{1F4F7} Now please <strong>upload your photo</strong> by clicking the \u{1F4CE} button below.',
          epic_not_found: 'EPIC Number not found. Please check and try again.',
          valid_fail: '\u274C Could not validate. Please try again.',
          start_over: 'Okay! Let\'s start over.<br><br>Please enter your <strong>EPIC Number</strong>:',
          yes_or_no: 'Please type <strong>Yes</strong> to generate or <strong>No</strong> to cancel.',
          ready_another: '\u{1F44B} Ready for another card?<br><br>\u{1F4F1} Please enter your <strong>10-digit mobile number</strong>:',
          card_generated: '\u{1F389} <strong>Your ID Card has been generated!</strong>',
          gen_failed: 'Generation failed. Please try again.',
          went_wrong: '\u274C Something went wrong. Please try again.',
          cancelled: 'Cancelled. Please enter your <strong>EPIC Number</strong> to try again:',
          photo_received: '\u{1F4F8} Photo received!<br><br>Ready to generate your <strong>ID Card</strong>?<br>',
          gen_card_btn: '\u2705 Generate Card',
          cancel_btn: '\u274C Cancel',
          photo_uploaded: 'Photo uploaded',
          placeholder_type: 'Type a message...',
          placeholder_mobile: 'Enter 10-digit mobile number...',
          placeholder_otp: 'Enter 6-digit OTP...',
          placeholder_epic: 'Enter EPIC Number...',
          placeholder_upload: 'Click \u{1F4CE} to upload photo...',
          placeholder_yes: 'Type "yes" to confirm...'
        },
        ta: {
          banner: '<strong>புரட்சித்தாய்க்கு வரவேற்கிறோம்!</strong><br>உங்கள் டிஜிட்டல் உறுப்பினர் அடையாள அட்டை உருவாக்கி<br><br>\u{1F44B} வணக்கம்! <strong>புரட்சித்தாய்</strong> அடையாள அட்டை உருவாக்கிக்கு வரவேற்கிறோம்.',
          start_btn: '<i class="bi bi-play-circle-fill me-1"></i> தொடங்குக (Start)',
          starting: '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span> தொடங்குகிறது...',
          ask_mobile: '\u{1F4F1} சரிபார்க்க உங்கள் <strong>10 இலக்க மொபைல் எண்ணை</strong> உள்ளிடவும்:',
          invalid_mobile: '\u274C சரியான <strong>10 இலக்க மொபைல் எண்ணை</strong> உள்ளிடவும்.',
          ask_otp: '\u{1F4DE} <strong>+91 {m}</strong> எண்ணிற்கு <strong>OTP அழைப்பு</strong> வரும்.<br><br>அழைப்பில் கேட்கும் <strong>6 இலக்க OTP</strong> யை உள்ளிடவும்:',
          otp_fail: '\u274C OTP அனுப்ப முடியவில்லை. மீண்டும் முயற்சிக்கவும்.',
          invalid_otp: '\u274C சரியான <strong>6 இலக்க OTP</strong> யை உள்ளிடவும்.',
          otp_verified_exists: '\u2705 மொபைல் எண் சரிபார்க்கப்பட்டது! உங்கள் <strong>அடையாள அட்டை</strong> முன்பே உருவாக்கப்பட்டுள்ளது.',
          download_btn: '<i class="bi bi-download"></i> அட்டையை பதிவிறக்கு',
          type_anything: '<em style="color:#667781">புதிய அட்டை உருவாக்க எதையாவது தட்டச்சு செய்யவும்.</em>',
          ask_epic: '\u2705 மொபைல் எண் சரிபார்க்கப்பட்டது!<br><br>உங்கள் <strong>வாக்காளர் எண்ணை (EPIC)</strong> உள்ளிடவும்:',
          invalid_otp_res: 'தவறான OTP. மீண்டும் முயற்சிக்கவும்.',
          verif_fail: '\u274C சரிபார்க்க முடியவில்லை. மீண்டும் முயற்சிக்கவும்.',
          voter_found: '\u2705 <strong>வாக்காளர் விபரம் கிடைத்தது!</strong>',
          upload_photo: '\u{1F4F7} இப்போது கீழே உள்ள \u{1F4CE} பட்டனை கிளிக் செய்து <strong>உங்கள் புகைப்படத்தை பதிவேற்றவும்</strong>.',
          epic_not_found: 'வாக்காளர் எண் கிடைக்கவில்லை. சரிபார்த்து மீண்டும் முயற்சிக்கவும்.',
          valid_fail: '\u274C உறுதிப்படுத்த முடியவில்லை. மீண்டும் முயற்சிக்கவும்.',
          start_over: 'சரி! மீண்டும் தொடங்குவோம்.<br><br>உங்கள் <strong>வாக்காளர் எண்ணை</strong> உள்ளிடவும்:',
          yes_or_no: 'உருவாக்க <strong>Yes</strong> அல்லது ரத்து செய்ய <strong>No</strong> என தட்டச்சு செய்யவும்.',
          ready_another: '\u{1F44B} மற்றொரு அட்டை உருவாக்க தயாரா?<br><br>\u{1F4F1} உங்கள் <strong>10 இலக்க மொபைல் எண்ணை</strong> உள்ளிடவும்:',
          card_generated: '\u{1F389} <strong>உங்கள் அடையாள அட்டை உருவாக்கப்பட்டது!</strong>',
          gen_failed: 'அட்டை உருவாக்க முடியவில்லை. மீண்டும் முயற்சிக்கவும்.',
          went_wrong: '\u274C ஏதோ தவறு நடந்துவிட்டது. மீண்டும் முயற்சிக்கவும்.',
          cancelled: 'ரத்து செய்யப்பட்டது. மீண்டும் முயற்சிக்க உங்கள் <strong>வாக்காளர் எண்ணை</strong> உள்ளிடவும்:',
          photo_received: '\u{1F4F8} புகைப்படம் பெறப்பட்டது!<br><br>உங்கள் <strong>அடையாள அட்டையை</strong> உருவாக்கலாமா?<br>',
          gen_card_btn: '\u2705 அட்டையை உருவாக்கு',
          cancel_btn: '\u274C ரத்து செய்',
          photo_uploaded: 'புகைப்படம் பதிவேற்றப்பட்டது',
          placeholder_type: 'செய்தியை தட்டச்சு செய்யவும்...',
          placeholder_mobile: '10 இலக்க மொபைல் எண்ணை உள்ளிடவும்...',
          placeholder_otp: '6 இலக்க OTPஐ உள்ளிடவும்...',
          placeholder_epic: 'வாக்காளர் எண்ணை உள்ளிடவும்...',
          placeholder_upload: 'Photo பதிவேற்ற \u{1F4CE} கிளிக் செய்யவும்...',
          placeholder_yes: 'உறுதிப்படுத்த "yes" என தட்டச்சு செய்யவும்...'
        }
      };

      let lang = 'en';
      const t = (k, vars={}) => {
        let str = i18n[lang][k] || k;
        for (let [vk, vv] of Object.entries(vars)) { str = str.replace('{'+vk+'}', vv); }
        return str;
      };

      /* ── State Machine ── */
      const S = {
        WELCOME: 0, AWAIT_MOBILE: 1, AWAIT_OTP: 2,
        AWAIT_EPIC: 3, AWAIT_PHOTO: 4, CONFIRM: 5,
        GENERATING: 6, DONE: 7
      };
      let state = S.WELCOME;
      let mobile = '', epic = '', voter = null, photoFile = null;

      /* ── DOM ── */
      const chatEl = document.getElementById('chatMessages');
      const input = document.getElementById('messageInput');
      const sendBtn = document.getElementById('sendBtn');
      const attachBtn = document.getElementById('attachBtn');
      const photoInput = document.getElementById('photoInput');
      const langToggleBtn = document.getElementById('langToggle');

      /* ── Helpers ── */
      const now = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const scroll = () => setTimeout(() => chatEl.scrollTop = chatEl.scrollHeight, 50);

      function addBubble(html, type, extraClass) {
        const div = document.createElement('div');
        div.className = 'message ' + type + (extraClass ? ' ' + extraClass : '');

        if (type === 'bot') {
          const img = document.createElement('img');
          img.src = 'https://puratchithaai.com/wp-content/uploads/sb-instagram-feed-images/307806566_619559223165116_5114435583162313103_nfull.jpg';
          img.className = 'bot-avatar-img';
          div.appendChild(img);
        }

        const b = document.createElement('div');
        b.className = 'bubble';
        b.innerHTML = html + '<span class="time">' + now() + '</span>';
        div.appendChild(b);

        if (type === 'user') {
          const userIcon = document.createElement('div');
          userIcon.className = 'user-avatar-svg';
          userIcon.innerHTML = '<i class="bi bi-person-fill"></i>';
          div.appendChild(userIcon);
        }

        chatEl.appendChild(div);
        scroll();
      }
      const botMsg = (h, cls) => addBubble(h, 'bot', cls);
      const userMsg = (h, cls) => addBubble(h, 'user', cls);

      function addBanner(imgUrl, htmlKey, withStartBtn) {
        const div = document.createElement('div');
        div.className = 'message bot banner-message';
        div.innerHTML =
          '<div class="bubble">' +
          '<img src="' + imgUrl + '" alt="Banner" onerror="this.style.display=\\\'none\\\'">' +
          '<div class="banner-text">' + t(htmlKey) + '<span class="time">' + now() + '</span></div>' +
          (withStartBtn ? '<div class="banner-action"><button class="btn-reply" id="bannerStartBtn">' + t('start_btn') + '</button></div>' : '') +
          '</div>';
        chatEl.appendChild(div);

        if (withStartBtn) {
          document.getElementById('bannerStartBtn').onclick = function () {
            this.disabled = true;
            this.innerHTML = t('starting');
            input.value = 'Hi';
            handleSend();
          };
        }
        scroll();
      }

      function addDateChip() {
        const d = document.createElement('div');
        d.className = 'date-chip';
        d.textContent = new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
        chatEl.appendChild(d);
      }

      let typingEl = null;
      function showTyping() {
        if (typingEl) return;
        typingEl = document.createElement('div');
        typingEl.className = 'message bot typing-indicator';
        typingEl.innerHTML = '<img class="bot-avatar-img" src="https://puratchithaai.com/wp-content/uploads/sb-instagram-feed-images/307806566_619559223165116_5114435583162313103_nfull.jpg"><div class="bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>';
        chatEl.appendChild(typingEl);
        scroll();
      }
      function hideTyping() {
        if (typingEl) { typingEl.remove(); typingEl = null; }
      }

      function botReply(html, delay) {
        return new Promise(resolve => {
          showTyping();
          setTimeout(() => { hideTyping(); botMsg(html); resolve(); }, delay || 800);
        });
      }

      function setNumeric(placeholder_key) {
        input.type = 'tel';
        input.inputMode = 'numeric';
        input.pattern = '[0-9]*';
        input.placeholder = t(placeholder_key) || '';
      }
      function setText(placeholder_key) {
        input.type = 'text';
        input.inputMode = 'text';
        input.removeAttribute('pattern');
        input.placeholder = t(placeholder_key) || '';
      }
      function showAttach() { attachBtn.classList.add('visible'); }
      function hideAttach() { attachBtn.classList.remove('visible'); }
      function lockInput() { input.disabled = true; sendBtn.disabled = true; }
      function unlockInput() { input.disabled = false; sendBtn.disabled = false; input.focus(); }

      function updateCurrentPlaceholder() {
        if (state === S.WELCOME || state === S.DONE) { setText('placeholder_type'); }
        else if (state === S.AWAIT_MOBILE) { setNumeric('placeholder_mobile'); }
        else if (state === S.AWAIT_OTP) { setNumeric('placeholder_otp'); }
        else if (state === S.AWAIT_EPIC) { setText('placeholder_epic'); }
        else if (state === S.AWAIT_PHOTO) { setText('placeholder_upload'); }
        else if (state === S.CONFIRM) { setText('placeholder_yes'); }
      }

      /* ── Banner URL ── */
      const BANNER = "/static/banner.jpg";

      /* ── Init ── */
      async function init() {
        addDateChip();
        addBanner(BANNER, 'banner', true);
        updateCurrentPlaceholder();
      }

      /* ── API fetch wrapper ── */
      async function api(url, body, isForm) {
        const opts = { method: 'POST' };
        if (isForm) {
          opts.body = body;  // FormData
        } else {
          opts.headers = { 'Content-Type': 'application/json' };
          opts.body = JSON.stringify(body);
        }
        const r = await fetch(url, opts);
        return r.json();
      }

      /* ── Main Handler ── */
      async function handleSend() {
        const txt = input.value.trim();
        if (!txt && state !== S.AWAIT_PHOTO) return;
        input.value = '';

        if (state === S.WELCOME) {
          userMsg(txt);
          state = S.AWAIT_MOBILE;
          setNumeric('placeholder_mobile');
          await botReply(t('ask_mobile'), 900);

        } else if (state === S.AWAIT_MOBILE) {
          const m = txt.replace(/\D/g, '');
          if (m.length !== 10) {
            userMsg(txt);
            await botReply(t('invalid_mobile'), 600);
            return;
          }
          userMsg(m);
          mobile = m;
          lockInput();

          try {
            // Always send OTP — every time
            showTyping();
            const otp = await api('/api/chat/send-otp', { mobile: m });
            hideTyping();
            if (otp.success) {
              state = S.AWAIT_OTP;
              setNumeric('placeholder_otp');
              unlockInput();
              await botReply(t('ask_otp', {m: m}), 800);
            } else {
              unlockInput();
              await botReply(t('otp_fail'), 600);
            }
          } catch (e) {
            hideTyping(); unlockInput();
            await botReply(t('went_wrong'), 600);
          }

        } else if (state === S.AWAIT_OTP) {
          const o = txt.replace(/\D/g, '');
          if (o.length !== 6) {
            userMsg(txt);
            await botReply(t('invalid_otp'), 600);
            return;
          }
          userMsg(o);
          lockInput();
          try {
            const res = await api('/api/chat/verify-otp', { mobile, otp: o });
            if (res.success) {
              // Check if mobile already has a generated card
              if (res.has_card && res.card_url) {
                state = S.DONE;
                setText('placeholder_type');
                hideAttach();
                unlockInput();
                epic = res.epic_no || '';
                let h = t('otp_verified_exists');
                h += '<div class="card-preview"><img src="' + res.card_url + '" alt="ID Card"></div>';
                h += '<a href="/mycard/' + res.epic_no + '/download" class="download-btn" target="_blank">' + t('download_btn') + '</a>';
                h += '<br><br>' + t('type_anything');
                await botReply(h, 1200);
              } else {
                state = S.AWAIT_EPIC;
                setText('placeholder_epic');
                unlockInput();
                await botReply(t('ask_epic'), 800);
              }
            } else {
              unlockInput();
              const err = res.message || t('invalid_otp_res');
              await botReply('\u274C ' + err, 600);
            }
          } catch (e) {
            unlockInput();
            await botReply(t('verif_fail'), 600);
          }

        } else if (state === S.AWAIT_EPIC) {
          const ep = txt.trim().toUpperCase();
          if (!ep) return;
          userMsg(ep);
          lockInput();
          try {
            showTyping();
            const res = await api('/api/chat/validate-epic', { epic_no: ep });
            hideTyping();
            if (res.success) {
              epic = ep;
              voter = res.voter;
              state = S.AWAIT_PHOTO;
              setText('placeholder_upload');
              showAttach();
              unlockInput();

              // Build details HTML
              let h = t('voter_found') + '<div class="voter-details-card">';
              const skip = new Set(['_id', 'photo_url', 'card_url', 'verify_url', 'serial_number']);
              for (const [k, v] of Object.entries(res.voter)) {
                if (!v || skip.has(k)) continue;
                const lbl = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                h += '<div class="detail-row"><span class="detail-label">' + lbl + '</span><span class="detail-value">' + v + '</span></div>';
              }
              h += '</div><br>' + t('upload_photo');
              await botReply(h, 1000);
            } else {
              unlockInput();
              const err = res.message || t('epic_not_found');
              await botReply('\u274C ' + err, 600);
            }
          } catch (e) {
            hideTyping(); unlockInput();
            await botReply(t('valid_fail'), 600);
          }

        } else if (state === S.CONFIRM) {
          const lo = txt.toLowerCase();
          if (lo === 'yes' || lo === 'confirm' || lo === 'generate' || lo === 'y' || lo === 'ஆம்') {
            userMsg(txt);
            await doGenerate();
          } else if (lo === 'no' || lo === 'cancel' || lo === 'n' || lo === 'இல்லை') {
            userMsg(txt);
            state = S.AWAIT_EPIC;
            setText('placeholder_epic');
            hideAttach();
            photoFile = null;
            await botReply(t('start_over'), 700);
          } else {
            userMsg(txt);
            await botReply(t('yes_or_no'), 500);
          }

        } else if (state === S.DONE) {
          userMsg(txt);
          state = S.AWAIT_MOBILE;
          setNumeric('placeholder_mobile');
          hideAttach();
          photoFile = null;
          await botReply(t('ready_another'), 800);
        }
      }

      /* ── Card Generation ── */
      window.doGenerate = async function () {
        state = S.GENERATING;
        lockInput();

        try {
          const fd = new FormData();
          fd.append('epic_no', epic);
          fd.append('mobile', mobile);
          if (photoFile) fd.append('photo', photoFile);

          showTyping();
          const res = await api('/api/chat/generate-card', fd, true);
          hideTyping();

          if (res.success) {
            state = S.DONE;
            let h = t('card_generated');
            if (res.card_url) {
              h += '<div class="card-preview"><img src="' + res.card_url + '" alt="ID Card"></div>';
              h += '<a href="/mycard/' + epic + '/download" class="download-btn" target="_blank">' + t('download_btn') + '</a>';
            }
            h += '<br><br>' + t('type_anything');
            await botReply(h, 1500);
            setText('placeholder_type');
            hideAttach();
            unlockInput();
            photoFile = null;
          } else {
            state = S.CONFIRM;
            unlockInput();
            const err = res.message || t('gen_failed');
            await botReply('\u274C ' + err, 700);
          }
        } catch (e) {
          hideTyping();
          state = S.CONFIRM;
          unlockInput();
          await botReply(t('went_wrong'), 600);
        }
      };

      /* ── Cancel ── */
      window.doCancel = function () {
        state = S.AWAIT_EPIC;
        setText('placeholder_epic');
        hideAttach();
        photoFile = null;
        botReply(t('cancelled'), 500);
      };

      /* ── Photo Upload ── */
      attachBtn.addEventListener('click', () => {
        if (state === S.AWAIT_PHOTO) photoInput.click();
      });

      photoInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        photoFile = file;

        // Show preview
        const reader = new FileReader();
        reader.onload = async (ev) => {
          userMsg('<img src="' + ev.target.result + '" class="photo-thumb" alt="Photo"><br>' + t('photo_uploaded'));
          state = S.CONFIRM;
          hideAttach();
          setText('placeholder_yes');

          let h = t('photo_received');
          h += '<div class="action-buttons">';
          h += '<button class="action-btn confirm" onclick="doGenerate()">' + t('gen_card_btn') + '</button>';
          h += '<button class="action-btn cancel" onclick="doCancel()">' + t('cancel_btn') + '</button>';
          h += '</div>';
          await botReply(h, 900);
        };
        reader.readAsDataURL(file);
        photoInput.value = '';
      });

      /* ── Event Listeners ── */
      sendBtn.addEventListener('click', handleSend);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') handleSend(); });

      /* ── Theme Toggle ── */
      const themeToggleBtn = document.getElementById('themeToggle');
      themeToggleBtn.addEventListener('click', () => {
        document.body.classList.toggle('dark-mode');
        if (document.body.classList.contains('dark-mode')) {
          themeToggleBtn.classList.replace('bi-lightbulb', 'bi-moon-stars-fill');
        } else {
          themeToggleBtn.classList.replace('bi-moon-stars-fill', 'bi-lightbulb');
        }
      });
      
      /* ── Language Toggle ── */
      if (langToggleBtn) {
        langToggleBtn.addEventListener('click', () => {
          if (lang === 'en') {
            lang = 'ta';
            langToggleBtn.textContent = 'TA';
          } else {
            lang = 'en';
            langToggleBtn.textContent = 'EN';
          }
          updateCurrentPlaceholder();
          
          // Re-trigger the whole process so bot messages restart via user action language selected
          chatEl.innerHTML = '';
          state = S.WELCOME;
          epic = ''; mobile = ''; photoFile = null;
          input.value = '';
          init();
        });
      }

      /* ── Boot ── */
      window.addEventListener('load', init);
    })();"""

html = re.sub(r'\(function \(\) \{.*?\n\s*\}\)\(\);', lambda _: new_script, html, flags=re.DOTALL)

with open(r'c:\Users\Admin\Desktop\Politics\voter-id-generator\templates\user\chatbot.html', 'w', encoding='utf-8') as f:
    f.write(html)
