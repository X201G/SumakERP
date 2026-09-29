(() => {
    let displayId = 0;

    function formatIsoDate(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
        return match ? `${match[3]}/${match[2]}/${match[1]}` : "";
    }

    function parseDayFirstDate(value) {
        const match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(value.trim());
        if (!match) return "";

        const day = Number(match[1]);
        const month = Number(match[2]);
        const year = Number(match[3]);
        const candidate = new Date(year, month - 1, day);
        if (candidate.getFullYear() !== year || candidate.getMonth() !== month - 1 || candidate.getDate() !== day) {
            return "";
        }

        return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }

    function initializeDateInput(nativeInput) {
        if (nativeInput.dataset.dayFirstReady) return;
        nativeInput.dataset.dayFirstReady = "true";

        const labels = Array.from(nativeInput.labels || []);
        const displayInput = document.createElement("input");
        displayInput.type = "text";
        displayInput.className = "day-first-date-display";
        displayInput.placeholder = "DD/MM/YYYY";
        displayInput.inputMode = "numeric";
        displayInput.autocomplete = "off";
        displayInput.required = nativeInput.required;
        displayInput.setAttribute(
            "aria-label",
            nativeInput.getAttribute("aria-label") || labels.map(label => label.textContent.trim()).join(" ") || "Date (DD/MM/YYYY)"
        );
        if (nativeInput.id) {
            displayInput.id = `day-first-date-${++displayId}`;
            labels.forEach(label => {
                if (label.htmlFor === nativeInput.id) label.htmlFor = displayInput.id;
            });
        }

        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "day-first-date-trigger";
        trigger.setAttribute("aria-label", "Open calendar");
        const icon = document.createElement("span");
        icon.className = "day-first-calendar-icon";
        icon.setAttribute("aria-hidden", "true");
        trigger.appendChild(icon);

        const wrapper = document.createElement("span");
        wrapper.className = "day-first-date-control";
        nativeInput.parentNode.insertBefore(wrapper, nativeInput);
        wrapper.append(displayInput, trigger, nativeInput);

        nativeInput.required = false;
        nativeInput.tabIndex = -1;
        nativeInput.setAttribute("aria-hidden", "true");
        nativeInput.classList.add("day-first-date-native");
        displayInput.value = formatIsoDate(nativeInput.value);

        function setNativeValue(value, notify) {
            if (nativeInput.value === value) return;
            nativeInput.value = value;
            if (notify) {
                nativeInput.dispatchEvent(new Event("input", { bubbles: true }));
                nativeInput.dispatchEvent(new Event("change", { bubbles: true }));
            }
        }

        function updateNativeValue() {
            const value = displayInput.value.trim();
            if (!value) {
                setNativeValue("", !displayInput.required);
                displayInput.setCustomValidity("");
                return true;
            }

            const isoDate = parseDayFirstDate(value);
            if (!isoDate) {
                displayInput.setCustomValidity("Enter a valid date as DD/MM/YYYY.");
                return false;
            }

            if ((nativeInput.min && isoDate < nativeInput.min) || (nativeInput.max && isoDate > nativeInput.max)) {
                displayInput.setCustomValidity("Choose a date within the allowed range.");
                return false;
            }

            setNativeValue(isoDate, true);
            displayInput.setCustomValidity("");
            return true;
        }

        displayInput.addEventListener("input", updateNativeValue);
        displayInput.addEventListener("change", updateNativeValue);
        nativeInput.addEventListener("input", () => {
            displayInput.value = formatIsoDate(nativeInput.value);
            displayInput.setCustomValidity("");
        });
        nativeInput.addEventListener("change", () => {
            displayInput.value = formatIsoDate(nativeInput.value);
            displayInput.setCustomValidity("");
        });
        trigger.addEventListener("click", () => {
            try {
                if (typeof nativeInput.showPicker === "function") nativeInput.showPicker();
                else nativeInput.click();
            } catch (error) {
                nativeInput.click();
            }
        });
    }

    function initializeWithin(node) {
        if (node instanceof HTMLInputElement && node.type === "date") initializeDateInput(node);
        if (node.querySelectorAll) node.querySelectorAll('input[type="date"]').forEach(initializeDateInput);
    }

    initializeWithin(document);
    new MutationObserver(records => {
        records.forEach(record => record.addedNodes.forEach(initializeWithin));
    }).observe(document.body, { childList: true, subtree: true });
})();