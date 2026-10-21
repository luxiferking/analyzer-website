document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const uploadBtn = document.getElementById('uploadBtn');
    const uploadContent = document.getElementById('uploadContent');
    const imagePreview = document.getElementById('imagePreview');
    const analyzeBtn = document.getElementById('analyzeBtn');

    let selectedFile = null;

    // ─────────────────────────────────────────────────────
    // 1. Trigger file input on drop zone click
    // ─────────────────────────────────────────────────────
    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    // ─────────────────────────────────────────────────────
    // 2. Trigger file input on button click (prevent bubbling)
    // ─────────────────────────────────────────────────────
    uploadBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    // ─────────────────────────────────────────────────────
    // 3. Handle file selection
    // ─────────────────────────────────────────────────────
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });

    // ─────────────────────────────────────────────────────
    // 4. Drag & Drop
    // ─────────────────────────────────────────────────────
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    // ─────────────────────────────────────────────────────
    // 5. Process the file (show preview, reset old results)
    // ─────────────────────────────────────────────────────
    function handleFile(file) {
        if (!file.type.startsWith('image/')) {
            alert('Please upload an image file (PNG, JPG, etc.).');
            return;
        }

        selectedFile = file;

        const reader = new FileReader();
        reader.onload = (e) => {
            imagePreview.src = e.target.result;
            uploadContent.classList.add('hidden');
            imagePreview.classList.remove('hidden');

            // Reset all result panels from a previous analysis
            resetResults();

            // Enable the analyze button
            analyzeBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            analyzeBtn.classList.add('bg-blue-600', 'text-white');
        };
        reader.readAsDataURL(file);
    }

    // ─────────────────────────────────────────────────────
    // 6. Reset all results before a new run
    // ─────────────────────────────────────────────────────
    function resetResults() {
        // Hide the entire results container
        document.getElementById('resultsContainer').classList.add('hidden');

        // Hide annotated image
        const annoWrapper = document.getElementById('annotatedImageWrapper');
        if (annoWrapper) {
            annoWrapper.classList.add('hidden');
            const annoImg = document.getElementById('annotatedImage');
            if (annoImg) annoImg.src = "";
        }

        // Hide patterns wrapper
        const patWrapper = document.getElementById('patternsWrapper');
        if (patWrapper) {
            patWrapper.classList.add('hidden');
            const patList = document.getElementById('patternsList');
            if (patList) patList.innerHTML = "";
        }
    }

    // ─────────────────────────────────────────────────────
    // 7. Handle "Find me the perfect bet" click
    // ─────────────────────────────────────────────────────
    analyzeBtn.addEventListener('click', async () => {
        if (!selectedFile) {
            alert('Please upload or drop an image first!');
            return;
        }

        // Loading state
        analyzeBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> PatternPilot is analyzing...';
        analyzeBtn.disabled = true;

        const formData = new FormData();
        formData.append('file', selectedFile);

        try {
            console.log("--- SENDING REQUEST ---");
            const response = await fetch('http://127.0.0.1:8001/api/analyze', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `Server error: ${response.status}`);
            }

            const data = await response.json();
            const analysis = JSON.parse(data.analysis);

            console.log("--- AI ANALYSIS ---", analysis);
            console.log("--- MODE ---", data.mode);
            console.log("--- HAS ANNOTATED IMAGE? ---", !!data.annotated_image);

            // ─── Annotated image ──────────────────────
            const annoWrapper = document.getElementById('annotatedImageWrapper');
            const annoImg = document.getElementById('annotatedImage');
            if (data.annotated_image && annoWrapper && annoImg) {
                annoImg.src = "data:image/png;base64," + data.annotated_image;
                // Click to open in new tab
                annoImg.onclick = () => {
                    const win = window.open();
                    win.document.write(`
                        <title>Annotated Chart — PatternPilot</title>
                        <body style="margin:0;background:#0a0a0a;display:flex;justify-content:center;align-items:center;min-height:100vh;">
                            <img src="data:image/png;base64,${data.annotated_image}"
                                 style="max-width:98%;max-height:98vh;box-shadow:0 0 40px rgba(0,0,0,0.8);">
                        </body>
                    `);
                };
                annoWrapper.classList.remove('hidden');
            } else {
                if (annoWrapper) annoWrapper.classList.add('hidden');
            }

            // ─── Text results ─────────────────────────
            document.getElementById('resTrend').textContent = analysis.trend || "--";
            document.getElementById('resConfidence').textContent =
                `${analysis.confidence_score ?? "--"}%`;
            document.getElementById('resEntry').textContent = analysis.entry || "--";
            document.getElementById('resStopLoss').textContent = analysis.stop_loss || "--";
            document.getElementById('resTakeProfit').textContent = analysis.take_profit || "--";
            document.getElementById('resReasoning').textContent = analysis.reasoning || "--";

            // Color code the trend
            const trendEl = document.getElementById('resTrend');
            const tl = (analysis.trend || "").toLowerCase();
            if (tl.includes('bull')) {
                trendEl.className = 'text-lg font-bold text-green-600';
            } else if (tl.includes('bear')) {
                trendEl.className = 'text-lg font-bold text-red-600';
            } else {
                trendEl.className = 'text-lg font-bold text-yellow-600';
            }

            // ─── Pattern chips ────────────────────────
            const patWrapper = document.getElementById('patternsWrapper');
            const patList = document.getElementById('patternsList');
            const patterns = analysis.patterns_detected || [];

            if (patterns.length > 0 && patWrapper && patList) {
                patList.innerHTML = "";
                patterns.forEach(p => {
                    const chip = document.createElement('span');
                    chip.className = "text-xs font-medium px-3 py-1 rounded-full bg-white border border-purple-200 text-purple-700";
                    chip.textContent = p;
                    patList.appendChild(chip);
                });
                patWrapper.classList.remove('hidden');
            } else {
                if (patWrapper) patWrapper.classList.add('hidden');
            }

            // ─── Show the results container ───────────
            document.getElementById('resultsContainer').classList.remove('hidden');

        } catch (error) {
            console.error('--- ERROR ---', error);
            alert(`Error: ${error.message}`);
        } finally {
            // Reset button state
            analyzeBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Find me the perfect bet';
            analyzeBtn.disabled = false;
        }
    });
});