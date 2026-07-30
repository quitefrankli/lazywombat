    const oneLine = "Sentinel is User-as-a-Service for websites: synthetic customers that test navigation, aesthetic fit, and purchase readiness before real buyers bounce.";
    const progress = document.getElementById("progress");
    const copyPitch = document.getElementById("copyPitch");
    const buttons = [...document.querySelectorAll(".lab-btn")];
    const title = document.getElementById("personaTitle");
    const summary = document.getElementById("personaSummary");
    const score = document.getElementById("personaScore");
    const findings = document.getElementById("personaFindings");

    const personas = {
      coder: {
        title: "Vibe-coder launch check",
        summary: "Ships quickly, wants a plain answer: does the site feel credible enough to send traffic to today?",
        score: 70,
        findings: [
          ["Launch risk", "Hero explains the feature but not who should buy it."],
          ["Trust gap", "No pricing reassurance before the signup prompt."],
          ["Fast fix", "Add one concrete customer outcome above the fold."]
        ]
      },
      senior: {
        title: "Tech-illiterate senior",
        summary: "Needs obvious labels, low memory load, and strong reassurance before entering personal information.",
        score: 54,
        findings: [
          ["Navigation", "Icon-only controls hide critical next steps on mobile."],
          ["Comprehension", "Form errors explain what failed but not how to fix it."],
          ["Confidence", "Security and support cues appear too late in checkout."]
        ]
      },
      parent: {
        title: "Stay-at-home parent",
        summary: "Compares quickly, scans on a phone, and abandons when shipping, returns, or timing feels uncertain.",
        score: 63,
        findings: [
          ["Mobile scan", "Product comparison cards do not expose the decisive difference."],
          ["Time cost", "Delivery estimates appear after cart creation."],
          ["Tone", "Copy feels premium but not practical."]
        ]
      },
      frugal: {
        title: "Frugal immigrant buyer",
        summary: "Highly price-sensitive, trust-sensitive, and likely to compare total cost before committing.",
        score: 49,
        findings: [
          ["Price trust", "Discount message conflicts with checkout subtotal."],
          ["Risk", "Return policy is buried behind a generic footer link."],
          ["Action", "Show total landed cost before account creation."]
        ]
      }
    };

    function setPersona(key) {
      const data = personas[key];
      title.textContent = data.title;
      summary.textContent = data.summary;
      score.style.setProperty("--score-deg", `${Math.round(data.score * 3.6)}deg`);
      score.querySelector("span").textContent = data.score;
      findings.innerHTML = data.findings.map(([label, text]) => `
        <div class="finding">
          <span class="check">+</span>
          <span><b>${label}:</b> ${text}</span>
        </div>
      `).join("");
      buttons.forEach((btn) => btn.setAttribute("aria-pressed", String(btn.dataset.persona === key)));
    }

    function updateProgress() {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const pct = max > 0 ? window.scrollY / max : 0;
      progress.style.transform = `scaleX(${pct})`;
    }

    copyPitch.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(oneLine);
        copyPitch.textContent = "Copied";
        setTimeout(() => {
          copyPitch.textContent = "Copy one-line pitch";
        }, 1400);
      } catch {
        copyPitch.textContent = oneLine;
      }
    });

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => setPersona(btn.dataset.persona));
    });

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });

    document.querySelectorAll(".reveal").forEach((el) => observer.observe(el));
    window.addEventListener("scroll", updateProgress, { passive: true });
    setPersona("coder");
    updateProgress();
