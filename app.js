/* =========================================================
   STATE
========================================================= */

let authMode = "login";

let currentUser = null;

let candidates = [];

let selectedCandidate = null;

let pendingExternalUrl = null;

let chatSocket = null;

let mediaRecorder = null;

let audioChunks = [];

let recording = false;

let silenceTimer = null;

let audioContext = null;

let analyser = null;

let microphoneSource = null;

let animationFrame = null;


/* =========================================================
   HELPERS
========================================================= */

const $ = (selector) =>
    document.querySelector(selector);


function showToast(message) {

    const toast = $("#toast");

    toast.textContent = message;

    toast.classList.add("show");

    setTimeout(() => {

        toast.classList.remove("show");

    }, 3500);
}


async function api(
    url,
    options = {}
) {

    const response = await fetch(
        url,
        options
    );

    let data = {};

    try {

        data = await response.json();

    } catch (_) {}

    if (!response.ok) {

        throw new Error(
            data.detail ||
            "Произошла ошибка."
        );
    }

    return data;
}


/* =========================================================
   AUTH
========================================================= */

function setAuthMode(mode) {

    authMode = mode;

    $("#loginTab")
        .classList.toggle(
            "active",
            mode === "login"
        );

    $("#registerTab")
        .classList.toggle(
            "active",
            mode === "register"
        );

    $("#authButtonText").textContent =
        mode === "login"
            ? "Войти"
            : "Создать аккаунт";

    $("#authError").textContent = "";
}


$("#loginTab").addEventListener(
    "click",
    () => setAuthMode("login")
);

$("#registerTab").addEventListener(
    "click",
    () => setAuthMode("register")
);


$("#authForm").addEventListener(
    "submit",
    async (event) => {

        event.preventDefault();

        const username =
            $("#username").value.trim();

        const password =
            $("#password").value;

        const form = new FormData();

        form.append(
            "username",
            username
        );

        form.append(
            "password",
            password
        );

        try {

            const endpoint =
                authMode === "login"
                    ? "/api/login"
                    : "/api/register";

            const data = await api(
                endpoint,
                {
                    method: "POST",
                    body: form
                }
            );

            currentUser = data.user;

            showApplication();

        } catch (error) {

            $("#authError").textContent =
                error.message;
        }
    }
);


async function checkAuthentication() {

    try {

        const data = await api(
            "/api/me"
        );

        if (data.user) {

            currentUser = data.user;

            showApplication();

        } else {

            showAuth();

        }

    } catch (_) {

        showAuth();
    }
}


function showAuth() {

    $("#authScreen")
        .classList.remove("hidden");

    $("#app")
        .classList.add("hidden");
}


function showApplication() {

    $("#authScreen")
        .classList.add("hidden");

    $("#app")
        .classList.remove("hidden");

    $("#currentUser").textContent =
        currentUser.username;

    loadCandidates();

    loadResults();

    loadChat();

    connectChat();
}


$("#logoutButton").addEventListener(
    "click",
    async () => {

        await api(
            "/api/logout",
            {
                method: "POST"
            }
        );

        currentUser = null;

        if (chatSocket) {

            chatSocket.close();
        }

        showAuth();
    }
);


/* =========================================================
   NAVIGATION
========================================================= */

document
    .querySelectorAll(".nav-button")
    .forEach(button => {

        button.addEventListener(
            "click",
            () => {

                const section =
                    button.dataset.section;

                document
                    .querySelectorAll(
                        ".nav-button"
                    )
                    .forEach(item => {

                        item.classList.toggle(
                            "active",
                            item === button
                        );

                    });

                document
                    .querySelectorAll(
                        ".section"
                    )
                    .forEach(item => {

                        item.classList.toggle(
                            "active",
                            item.id === section
                        );

                    });

                if (
                    section === "results"
                ) {

                    loadResults();
                }
            }
        );

    });


/* =========================================================
   CANDIDATES
========================================================= */

async function loadCandidates() {

    try {

        const data = await api(
            "/api/candidates"
        );

        candidates =
            data.candidates;

        renderCandidates();

    } catch (error) {

        showToast(
            error.message
        );
    }
}


function renderCandidates() {

    const grid =
        $("#candidateGrid");

    grid.innerHTML = "";

    candidates.forEach(
        candidate => {

            const card =
                document.createElement(
                    "div"
                );

            card.className =
                "candidate-card";

            card.innerHTML = `
                ${
                    candidate.image_url
                    ? `
                        <img
                            class="candidate-image"
                            src="${candidate.image_url}"
                        >
                    `
                    : `
                        <div class="empty-candidate">
                            ${candidate.slot}
                        </div>
                    `
                }

                <div class="candidate-info">

                    <div class="candidate-number">
                        КАНДИДАТ №${candidate.slot}
                    </div>

                    ${
                        candidate.name
                        ? `
                            <div class="candidate-name">
                                ${escapeHtml(
                                    candidate.name
                                )}
                            </div>
                        `
                        : `
                            <div class="empty-name">
                                Кандидат пока не добавлен
                            </div>
                        `
                    }

                </div>
            `;

            card.addEventListener(
                "click",
                () => openCandidate(candidate)
            );

            grid.appendChild(card);

        }
    );
}


/* =========================================================
   CANDIDATE MODAL
========================================================= */

async function openCandidate(
    candidate
) {

    selectedCandidate =
        candidate;

    const modal =
        $("#candidateModal");

    const content =
        $("#candidateModalContent");

    content.innerHTML = `
        ${
            candidate.image_url
            ? `
                <img
                    class="modal-candidate-image"
                    src="${candidate.image_url}"
                >
            `
            : `
                <div class="empty-candidate">
                    ${candidate.slot}
                </div>
            `
        }

        <div class="candidate-number">
            КАНДИДАТ №${candidate.slot}
        </div>

        <h2 class="modal-candidate-title">
            ${
                candidate.name
                ? escapeHtml(candidate.name)
                : "Кандидат №" + candidate.slot
            }
        </h2>

        <p>
            ${
                candidate.description
                ? escapeHtml(
                    candidate.description
                )
                : "Информация о кандидате пока не добавлена."
            }
        </p>

        <button
            id="voteButton"
            class="vote-button"
        >
            ПРОГОЛОСОВАТЬ
        </button>

        <div class="comments">

            <h3>
                Комментарии
            </h3>

            <div id="commentsList">
                Загрузка...
            </div>

            <form
                id="commentForm"
                class="comment-form"
            >

                <input
                    id="commentInput"
                    placeholder="Ваш комментарий..."
                    maxlength="2000"
                >

                <button
                    class="send-button"
                    type="submit"
                >
                    →
                </button>

            </form>

        </div>
    `;

    modal.classList.remove(
        "hidden"
    );

    $("#voteButton")
        .addEventListener(
            "click",
            voteForCandidate
        );

    $("#commentForm")
        .addEventListener(
            "submit",
            submitComment
        );

    loadComments(
        candidate.id
    );
}


$("#closeCandidateModal")
    .addEventListener(
        "click",
        closeCandidate
    );


document
    .querySelector(
        "#candidateModal .modal-backdrop"
    )
    .addEventListener(
        "click",
        closeCandidate
    );


function closeCandidate() {

    $("#candidateModal")
        .classList.add(
            "hidden"
        );
}


async function voteForCandidate() {

    if (!selectedCandidate)
        return;

    try {

        await api(
            `/api/vote/${selectedCandidate.id}`,
            {
                method: "POST"
            }
        );

        showToast(
            "Ваш голос принят."
        );

        $("#voteButton").disabled =
            true;

        $("#voteButton").textContent =
            "ГОЛОС ПРИНЯТ";

        loadResults();

    } catch (error) {

        showToast(
            error.message
        );
    }
}


async function loadComments(
    candidateId
) {

    try {

        const data = await api(
            `/api/candidates/${candidateId}/comments`
        );

        const list =
            $("#commentsList");

        list.innerHTML = "";

        if (!data.comments.length) {

            list.innerHTML = `
                <p class="empty-name">
                    Комментариев пока нет.
                </p>
            `;

            return;
        }

        data.comments.forEach(
            comment => {

                const element =
                    document.createElement(
                        "div"
                    );

                element.className =
                    "comment";

                element.innerHTML = `
                    <div class="comment-user">
                        ${escapeHtml(
                            comment.username
                        )}
                    </div>

                    <div class="comment-text">
                        ${escapeHtml(
                            comment.text
                        )}
                    </div>
                `;

                list.appendChild(
                    element
                );
            }
        );

    } catch (error) {

        showToast(
            error.message
        );
    }
}


async function submitComment(
    event
) {

    event.preventDefault();

    const input =
        $("#commentInput");

    const text =
        input.value.trim();

    if (!text)
        return;

    try {

        await api(
            `/api/candidates/${selectedCandidate.id}/comments`,
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    text
                })
            }
        );

        input.value = "";

        loadComments(
            selectedCandidate.id
        );

    } catch (error) {

        showToast(
            error.message
        );
    }
}


/* =========================================================
   RESULTS
========================================================= */

async function loadResults() {

    try {

        const data = await api(
            "/api/results"
        );

        $("#totalVotes")
            .textContent =
            data.total_votes;

        const list =
            $("#resultsList");

        list.innerHTML = "";

        data.candidates.forEach(
            candidate => {

                const row =
                    document.createElement(
                        "div"
                    );

                row.className =
                    "result-row";

                row.innerHTML = `
                    <div class="result-top">

                        <div class="result-name">
                            ${
                                candidate.name
                                ? escapeHtml(
                                    candidate.name
                                )
                                : "Кандидат №" +
                                  candidate.slot
                            }
                        </div>

                        <div class="result-numbers">
                            ${candidate.votes}
                            голосов ·
                            ${candidate.percentage}%
                        </div>

                    </div>

                    <div class="progress">

                        <div
                            class="progress-fill"
                            style="width:${candidate.percentage}%"
                        ></div>

                    </div>
                `;

                list.appendChild(row);
            }
        );

        if (data.status === "ended") {

            $("#electionStatus")
                .textContent =
                "ГОЛОСОВАНИЕ ЗАВЕРШЕНО";
        }

    } catch (error) {

        showToast(
            error.message
        );
    }
}


/* =========================================================
   CHAT
========================================================= */

async function loadChat() {

    try {

        const data = await api(
            "/api/chat"
        );

        const container =
            $("#chatMessages");

        container.innerHTML = "";

        data.messages.forEach(
            message =>
                renderChatMessage(
                    message
                )
        );

        container.scrollTop =
            container.scrollHeight;

    } catch (error) {

        showToast(
            error.message
        );
    }
}


function connectChat() {

    if (chatSocket) {

        try {
            chatSocket.close();
        } catch (_) {}
    }

    const protocol =
        location.protocol === "https:"
            ? "wss:"
            : "ws:";

    chatSocket =
        new WebSocket(
            `${protocol}//${location.host}/ws/chat`
        );

    chatSocket.onopen = () => {

        chatSocket.send(
            "connected"
        );
    };

    chatSocket.onmessage =
        event => {

            const message =
                JSON.parse(
                    event.data
                );

            renderChatMessage(
                message
            );

            const container =
                $("#chatMessages");

            container.scrollTop =
                container.scrollHeight;
        };
}


function renderChatMessage(
    message
) {

    const container =
        $("#chatMessages");

    const element =
        document.createElement(
            "div"
        );

    element.className =
        "chat-message";

    const safeText =
        linkify(
            message.text || ""
        );

    element.innerHTML = `
        <div class="chat-author">
            ${escapeHtml(
                message.username
            )}
        </div>

        <div class="chat-bubble">
            ${safeText}

            ${
                message.image_url
                ? `
                    <img
                        class="chat-image"
                        src="${message.image_url}"
                    >
                `
                : ""
            }
        </div>
    `;

    container.appendChild(
        element
    );
}


$("#chatForm")
    .addEventListener(
        "submit",
        async event => {

            event.preventDefault();

            const input =
                $("#chatInput");

            const fileInput =
                $("#chatImage");

            const text =
                input.value.trim();

            let imageUrl = null;

            try {

                if (
                    fileInput.files.length
                ) {

                    const form =
                        new FormData();

                    form.append(
                        "image",
                        fileInput.files[0]
                    );

                    const result =
                        await api(
                            "/api/chat/upload",
                            {
                                method: "POST",
                                body: form
                            }
                        );

                    imageUrl =
                        result.url;
                }

                if (
                    !text &&
                    !imageUrl
                ) {
                    return;
                }

                await api(
                    "/api/chat",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body: JSON.stringify({
                            text,
                            image_url:
                                imageUrl
                        })
                    }
                );

                input.value = "";

                fileInput.value = "";

            } catch (error) {

                showToast(
                    error.message
                );
            }
        }
    );


/* =========================================================
   LINK SYSTEM
========================================================= */

function linkify(text) {

    const escaped =
        escapeHtml(text);

    return escaped.replace(
        /(https?:\/\/[^\s<]+)/gi,
        match => {

            return `
                <a
                    href="#"
                    class="chat-link"
                    data-external-url="${encodeURIComponent(match)}"
                >
                    ${match}
                </a>
            `;
        }
    );
}


document.addEventListener(
    "click",
    event => {

        const link =
            event.target.closest(
                ".chat-link"
            );

        if (!link)
            return;

        event.preventDefault();

        const url =
            decodeURIComponent(
                link.dataset.externalUrl
            );

        openExternalLink(url);
    }
);


function openExternalLink(url) {

    try {

        const parsed =
            new URL(url);

        const sameHost =
            parsed.host ===
            window.location.host;

        if (
            sameHost ||
            parsed.hostname ===
            window.location.hostname
        ) {

            window.location.href =
                url;

            return;
        }

    } catch (_) {}

    pendingExternalUrl =
        url;

    $("#warningUrl")
        .textContent =
        url;

    $("#externalLinkModal")
        .classList.remove(
            "hidden"
        );
}


$("#cancelExternalLink")
    .addEventListener(
        "click",
        () => {

            pendingExternalUrl =
                null;

            $("#externalLinkModal")
                .classList.add(
                    "hidden"
                );
        }
    );


$("#confirmExternalLink")
    .addEventListener(
        "click",
        () => {

            if (
                pendingExternalUrl
            ) {

                window.open(
                    pendingExternalUrl,
                    "_blank",
                    "noopener,noreferrer"
                );
            }

            pendingExternalUrl =
                null;

            $("#externalLinkModal")
                .classList.add(
                    "hidden"
                );
        }
    );


/* =========================================================
   AI CHAT
========================================================= */

$("#aiForm")
    .addEventListener(
        "submit",
        async event => {

            event.preventDefault();

            const input =
                $("#aiInput");

            const message =
                input.value.trim();

            if (!message)
                return;

            input.value = "";

            addAiMessage(
                message,
                "user"
            );

            try {

                const result =
                    await api(
                        "/api/ai",
                        {
                            method: "POST",
                            headers: {
                                "Content-Type":
                                    "application/json"
                            },
                            body: JSON.stringify({
                                message
                            })
                        }
                    );

                addAiMessage(
                    result.answer,
                    "assistant"
                );

                speak(
                    result.answer
                );

                if (
                    result.type ===
                    "image" &&
                    result.image_prompt
                ) {

                    const image =
                        await api(
                            "/api/ai/image",
                            {
                                method: "POST",
                                headers: {
                                    "Content-Type":
                                        "application/json"
                                },
                                body:
                                    JSON.stringify({
                                        prompt:
                                            result.image_prompt
                                    })
                            }
                        );

                    addAiImage(
                        image.image_url
                    );
                }

                if (
                    result.type ===
                    "weather" &&
                    result.weather
                ) {

                    addWeatherCard(
                        result.weather
                    );
                }

            } catch (error) {

                addAiMessage(
                    "Ошибка: " +
                    error.message,
                    "assistant"
                );
            }
        }
    );


function addAiMessage(
    text,
    type
) {

    const container =
        $("#aiMessages");

    const element =
        document.createElement(
            "div"
        );

    element.className =
        "ai-message " + type;

    element.textContent =
        text;

    container.appendChild(
        element
    );

    container.scrollTop =
        container.scrollHeight;
}


function addAiImage(
    url
) {

    const container =
        $("#aiMessages");

    const element =
        document.createElement(
            "div"
        );

    element.className =
        "ai-message assistant";

    element.innerHTML = `
        <img
            src="${url}"
            style="
                max-width:100%;
                border-radius:15px;
                display:block;
            "
        >
    `;

    container.appendChild(
        element
    );

    container.scrollTop =
        container.scrollHeight;
}


function addWeatherCard(
    weather
) {

    const container =
        $("#aiMessages");

    const element =
        document.createElement(
            "div"
        );

    element.className =
        "ai-message assistant";

    element.innerHTML = `
        <div
            style="
                font-size:32px;
                margin-bottom:8px;
            "
        >
            ${weatherEmoji(
                weather.icon
            )}
        </div>

        <strong>
            ${escapeHtml(
                weather.city
            )}
        </strong>

        <div
            style="
                font-size:32px;
                margin-top:8px;
            "
        >
            ${weather.temperature}°C
        </div>

        <div>
            ${escapeHtml(
                weather.description
            )}
        </div>

        <div
            style="
                color:#8792ad;
                margin-top:8px;
            "
        >
            Влажность:
            ${weather.humidity}%
        </div>
    `;

    container.appendChild(
        element
    );
}


function weatherEmoji(
    icon
) {

    if (!icon)
        return "🌤️";

    if (
        icon.startsWith("01")
    )
        return "☀️";

    if (
        icon.startsWith("02")
    )
        return "🌤️";

    if (
        icon.startsWith("03") ||
        icon.startsWith("04")
    )
        return "☁️";

    if (
        icon.startsWith("09") ||
        icon.startsWith("10")
    )
        return "🌧️";

    if (
        icon.startsWith("11")
    )
        return "⛈️";

    if (
        icon.startsWith("13")
    )
        return "❄️";

    return "🌫️";
}


/* =========================================================
   VOICE / ORB
========================================================= */

$("#aiOrb")
    .addEventListener(
        "click",
        async () => {

            if (recording) {

                await stopRecording();

            } else {

                await startRecording();
            }
        }
    );


async function startRecording() {

    if (
        !navigator.mediaDevices ||
        !navigator.mediaDevices.getUserMedia
    ) {

        showToast(
            "Браузер не поддерживает запись."
        );

        return;
    }

    try {

        const stream =
            await navigator
                .mediaDevices
                .getUserMedia({
                    audio: true
                });

        audioChunks = [];

        mediaRecorder =
            new MediaRecorder(
                stream
            );

        mediaRecorder.ondataavailable =
            event => {

                if (
                    event.data.size
                ) {

                    audioChunks.push(
                        event.data
                    );
                }
            };

        mediaRecorder.onstop =
            async () => {

                stream
                    .getTracks()
                    .forEach(
                        track =>
                            track.stop()
                    );

                await processVoice();
            };

        mediaRecorder.start();

        recording = true;

        $("#aiOrb")
            .classList.add(
                "active"
            );

        $("#aiOrbStatus")
            .textContent =
            "Слушаю...";

        startAudioVisualizer(
            stream
        );

    } catch (error) {

        showToast(
            "Не удалось получить доступ к микрофону."
        );
    }
}


async function stopRecording() {

    if (
        !mediaRecorder ||
        !recording
    ) {
        return;
    }

    recording = false;

    clearTimeout(
        silenceTimer
    );

    if (
        animationFrame
    ) {

        cancelAnimationFrame(
            animationFrame
        );
    }

    if (
        audioContext
    ) {

        try {
            await audioContext.close();
        } catch (_) {}
    }

    mediaRecorder.stop();

    $("#aiOrb")
        .classList.remove(
            "active"
        );

    $("#aiOrbStatus")
        .textContent =
        "Обрабатываю...";
}


async function processVoice() {

    const blob =
        new Blob(
            audioChunks,
            {
                type:
                    "audio/webm"
            }
        );

    if (
        blob.size < 1000
    ) {

        $("#aiOrbStatus")
            .textContent =
            "Нажмите на шар";

        return;
    }

    const form =
        new FormData();

    form.append(
        "audio",
        blob,
        "voice.webm"
    );

    try {

        const transcription =
            await api(
                "/api/ai/voice",
                {
                    method: "POST",
                    body: form
                }
            );

        const text =
            transcription.text.trim();

        if (!text) {

            $("#aiOrbStatus")
                .textContent =
                "Речь не распознана.";

            return;
        }

        addAiMessage(
            text,
            "user"
        );

        const response =
            await api(
                "/api/ai",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body:
                        JSON.stringify({
                            message: text
                        })
                }
            );

        addAiMessage(
            response.answer,
            "assistant"
        );

        speak(
            response.answer
        );

        if (
            response.type ===
            "weather" &&
            response.weather
        ) {

            addWeatherCard(
                response.weather
            );
        }

        if (
            response.type ===
            "image" &&
            response.image_prompt
        ) {

            const image =
                await api(
                    "/api/ai/image",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body:
                            JSON.stringify({
                                prompt:
                                    response.image_prompt
                            })
                        }
                );

            addAiImage(
                image.image_url
            );
        }

    } catch (error) {

        addAiMessage(
            "Ошибка обработки голоса: " +
            error.message,
            "assistant"
        );

    } finally {

        $("#aiOrbStatus")
            .textContent =
            "Нажмите на шар";
    }
}


function startAudioVisualizer(
    stream
) {

    try {

        audioContext =
            new (
                window.AudioContext ||
                window.webkitAudioContext
            )();

        analyser =
            audioContext.createAnalyser();

        analyser.fftSize =
            256;

        microphoneSource =
            audioContext.createMediaStreamSource(
                stream
            );

        microphoneSource.connect(
            analyser
        );

        const data =
            new Uint8Array(
                analyser.frequencyBinCount
            );

        let quietSince = null;

        function update() {

            if (!recording)
                return;

            analyser.getByteTimeDomainData(
                data
            );

            let sum = 0;

            for (
                let i = 0;
                i < data.length;
                i++
            ) {

                const value =
                    (data[i] - 128) / 128;

                sum +=
                    value * value;
            }

            const volume =
                Math.sqrt(
                    sum / data.length
                );

            const scale =
                1 +
                Math.min(
                    volume * 2.8,
                    0.65
                );

            $("#aiOrb").style.transform =
                `scale(${scale})`;

            if (
                volume < 0.025
            ) {

                if (
                    quietSince === null
                ) {

                    quietSince =
                        Date.now();
                }

                if (
                    Date.now() -
                    quietSince >
                    1800
                ) {

                    stopRecording();

                    return;
                }

            } else {

                quietSince = null;
            }

            animationFrame =
                requestAnimationFrame(
                    update
                );
        }

        update();

    } catch (_) {}
}


/* =========================================================
   SPEECH
========================================================= */

function speak(text) {

    if (
        !window.speechSynthesis
    )
        return;

    window.speechSynthesis.cancel();

    const utterance =
        new SpeechSynthesisUtterance(
            cleanSpeechText(text)
        );

    utterance.lang =
        "ru-RU";

    utterance.rate =
        1;

    utterance.pitch =
        1;

    window.speechSynthesis.speak(
        utterance
    );
}


function cleanSpeechText(
    text
) {

    return text
        .replace(
            /IMAGE_PROMPT:[\s\S]*/i,
            ""
        )
        .replace(
            /[*_`#]/g,
            ""
        )
        .trim();
}


/* =========================================================
   MODERATOR
========================================================= */

document.addEventListener(
    "keydown",
    event => {

        if (
            event.shiftKey &&
            event.key.toLowerCase() === "p"
        ) {

            event.preventDefault();

            const consoleElement =
                $("#moderatorConsole");

            consoleElement
                .classList.toggle(
                    "hidden"
                );

            if (
                !consoleElement
                    .classList.contains(
                        "hidden"
                    )
            ) {

                $("#moderatorInput")
                    .focus();
            }
        }

        if (
            event.key === "Escape"
        ) {

            $("#moderatorConsole")
                .classList.add(
                    "hidden"
                );
        }
    }
);


$("#moderatorInput")
    .addEventListener(
        "keydown",
        async event => {

            if (
                event.key !== "Enter"
            )
                return;

            const command =
                event.target.value
                    .trim()
                    .toLowerCase();

            event.target.value = "";

            if (
                command === "!end" ||
                command === "/end"
            ) {

                const key =
                    prompt(
                        "Введите Moderator Key:"
                    );

                if (!key)
                    return;

                try {

                    await api(
                        "/api/election/end",
                        {
                            method: "POST",
                            headers: {
                                "X-Moderator-Key":
                                    key
                            }
                        }
                    );

                    showToast(
                        "Голосование завершено."
                    );

                    loadResults();

                    $("#moderatorConsole")
                        .classList.add(
                            "hidden"
                        );

                } catch (error) {

                    showToast(
                        error.message
                    );
                }
            }
        }
    );


/* =========================================================
   ESCAPE HTML
========================================================= */

function escapeHtml(
    value
) {

    return String(value)
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );
}


/* =========================================================
   START
========================================================= */

checkAuthentication();
