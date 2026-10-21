document.addEventListener('DOMContentLoaded', () => {
    const scanBtn = document.getElementById('scanBtn');
    const btnText = document.getElementById('btnText');

    const waitingState = document.getElementById('waitingState');
    const loadingState = document.getElementById('loadingState');
    const resultsContainer = document.getElementById('resultsContainer');

    const resAction = document.getElementById('resAction');
    const resSymbol = document.getElementById('resSymbol');
    const resEntry = document.getElementById('resEntry');
    const resStopLoss = document.getElementById('resStopLoss');
    const resTakeProfit = document.getElementById('resTakeProfit');
    const terminalOutput = document.getElementById('terminalOutput');

    scanBtn.addEventListener('click', async () => {
        waitingState.classList.add('hidden');
        resultsContainer.classList.add('hidden');
        resultsContainer.classList.remove('flex');

        loadingState.classList.remove('hidden');
        loadingState.classList.add('flex');

        btnText.innerText = "Scanning...";
        scanBtn.disabled = true;
        scanBtn.classList.add('opacity-75', 'cursor-not-allowed');

        try {
            // HERE IS THE NEW ENDPOINT FOR THE AGGRESSIVE BOT
            const response = await fetch('https://analyzer-website-y70x.onrender.com/api/scan_pure');
            const data = await response.json();

            loadingState.classList.add('hidden');
            loadingState.classList.remove('flex');

            if (data.status === "success") {
                resSymbol.innerText = data.symbol;
                resAction.innerText = `${data.action} (${data.strength})`;
                resEntry.innerText = `₹${data.entry}`;
                resStopLoss.innerText = `₹${data.stop_loss}`;
                resTakeProfit.innerText = `₹${data.target}`;

                terminalOutput.innerText = data.terminal_output;

                resultsContainer.classList.remove('hidden');
                resultsContainer.classList.add('flex');

            } else if (data.status === "wait") {
                resSymbol.innerText = "SCANNER";
                resAction.innerText = "NO TRADE";
                resAction.classList.add('text-rose-500');
                resEntry.innerText = "--";
                resStopLoss.innerText = "--";
                resTakeProfit.innerText = "--";

                terminalOutput.innerText = data.message;

                resultsContainer.classList.remove('hidden');
                resultsContainer.classList.add('flex');
            } else {
                alert("API Error: " + data.message);
                waitingState.classList.remove('hidden');
            }

        } catch (error) {
            loadingState.classList.add('hidden');
            loadingState.classList.remove('flex');
            waitingState.classList.remove('hidden');
            console.error("Fetch Error:", error);
            alert("Failed to connect to Python backend. Make sure your FastAPI server is running!");
        } finally {
            btnText.innerText = "Scan Momentum Now";
            scanBtn.disabled = false;
            scanBtn.classList.remove('opacity-75', 'cursor-not-allowed');
        }
    });
});