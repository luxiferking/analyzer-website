document.addEventListener('DOMContentLoaded', () => {
    const scanBtn = document.getElementById('scanBtn');
    const btnText = document.getElementById('btnText');

    // UI Sections
    const waitingState = document.getElementById('waitingState');
    const loadingState = document.getElementById('loadingState');
    const resultsContainer = document.getElementById('resultsContainer');

    // Data Elements
    const resAction = document.getElementById('resAction');
    const resSymbol = document.getElementById('resSymbol');
    const resEntry = document.getElementById('resEntry');
    const resStopLoss = document.getElementById('resStopLoss');
    const resTakeProfit = document.getElementById('resTakeProfit');
    const terminalOutput = document.getElementById('terminalOutput');

    scanBtn.addEventListener('click', async () => {
        // 1. Set Loading State
        waitingState.classList.add('hidden');
        resultsContainer.classList.add('hidden');
        resultsContainer.classList.remove('flex');

        loadingState.classList.remove('hidden');
        loadingState.classList.add('flex');

        btnText.innerText = "Scanning...";
        scanBtn.disabled = true;
        scanBtn.classList.add('opacity-75', 'cursor-not-allowed');

        try {
            // 2. Call the Python Backend
            // Make sure your python server is running on port 8000!
            const response = await fetch('https://analyzer-website-y70x.onrender.com/api/scan');
            const data = await response.json();

            // 3. Hide Loading State
            loadingState.classList.add('hidden');
            loadingState.classList.remove('flex');

            // 4. Handle the response
            if (data.status === "success") {
                // Update Cards
                resSymbol.innerText = data.symbol;
                resAction.innerText = `${data.action} (${data.strength})`;
                resEntry.innerText = `₹${data.entry}`;
                resStopLoss.innerText = `₹${data.stop_loss}`;
                resTakeProfit.innerText = `₹${data.target}`;

                // Update Terminal
                terminalOutput.innerText = data.terminal_output;

                // Show Results
                resultsContainer.classList.remove('hidden');
                resultsContainer.classList.add('flex');

            } else if (data.status === "wait") {
                // No valid trade found (NO_EDGE)
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
                // API Error
                alert("API Error: " + data.message);
                waitingState.classList.remove('hidden');
            }

        } catch (error) {
            // 5. Handle Connection Errors
            loadingState.classList.add('hidden');
            loadingState.classList.remove('flex');
            waitingState.classList.remove('hidden');

            console.error("Fetch Error:", error);
            alert("Failed to connect to Python backend. Make sure your FastAPI server is running!");
        } finally {
            // 6. Reset Button
            btnText.innerText = "Scan Market Now";
            scanBtn.disabled = false;
            scanBtn.classList.remove('opacity-75', 'cursor-not-allowed');
        }
    });
});