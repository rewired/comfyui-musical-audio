export function playheadPositionPercentage(currentTime, duration) {
    if (!Number.isFinite(duration) || duration <= 0) return null;
    const normalizedTime = Number.isFinite(currentTime) ? currentTime : 0;
    const clampedTime = Math.min(Math.max(normalizedTime, 0), duration);
    return (clampedTime / duration) * 100;
}

function hidePlayheadElement(element) {
    element.classList.remove("is-visible");
    element.style.removeProperty("--mau-playhead-position");
}

export function renderPlayheadElement(element, snapshot) {
    const percentage = playheadPositionPercentage(
        snapshot?.currentTime,
        snapshot?.duration,
    );
    if (percentage === null) {
        hidePlayheadElement(element);
        return null;
    }
    element.style.setProperty("--mau-playhead-position", `${percentage}%`);
    element.classList.add("is-visible");
    return percentage;
}

export function createPlayheadController({
    element,
    transport,
    runtime,
    requestFrame = globalThis.requestAnimationFrame?.bind(globalThis),
    cancelFrame = globalThis.cancelAnimationFrame?.bind(globalThis),
} = {}) {
    if (!element?.style || !element?.classList) {
        throw new TypeError("createPlayheadController requires a playhead element");
    }
    if (
        !transport
        || typeof transport.getCurrentTime !== "function"
        || typeof transport.getDuration !== "function"
        || typeof transport.isPlaying !== "function"
        || typeof transport.subscribe !== "function"
    ) {
        throw new TypeError("createPlayheadController requires an audio transport");
    }
    if (!runtime || typeof runtime !== "object") {
        throw new TypeError("createPlayheadController requires a runtime object");
    }
    if (typeof requestFrame !== "function" || typeof cancelFrame !== "function") {
        throw new TypeError("createPlayheadController requires animation-frame functions");
    }

    let playheadElement = element;
    let audioTransport = transport;
    let playheadRuntime = runtime;
    let requestPlayheadFrame = requestFrame;
    let cancelPlayheadFrame = cancelFrame;
    let destroyed = false;
    let unsubscribe = () => {};

    const cancelLoop = () => {
        if (!playheadRuntime || playheadRuntime.playheadAnimationFrameId === null) return;
        cancelPlayheadFrame(playheadRuntime.playheadAnimationFrameId);
        playheadRuntime.playheadAnimationFrameId = null;
    };

    const render = () => {
        if (destroyed || !playheadElement || !audioTransport) return null;
        return renderPlayheadElement(playheadElement, {
            currentTime: audioTransport.getCurrentTime(),
            duration: audioTransport.getDuration(),
        });
    };

    const scheduleLoop = () => {
        if (
            destroyed
            || !playheadRuntime
            || !audioTransport?.isPlaying()
            || playheadRuntime.playheadAnimationFrameId !== null
        ) {
            return;
        }
        playheadRuntime.playheadAnimationFrameId = requestPlayheadFrame(() => {
            if (destroyed || !playheadRuntime || !audioTransport) return;
            playheadRuntime.playheadAnimationFrameId = null;
            render();
            if (audioTransport.isPlaying()) scheduleLoop();
        });
    };

    const onTransportSnapshot = (snapshot) => {
        if (destroyed || !playheadElement || !playheadRuntime) return;
        renderPlayheadElement(playheadElement, snapshot);
        if (snapshot.playing) scheduleLoop();
        else cancelLoop();
    };

    unsubscribe = audioTransport.subscribe(onTransportSnapshot);

    const clear = () => {
        if (destroyed || !playheadElement) return;
        cancelLoop();
        hidePlayheadElement(playheadElement);
    };

    const destroy = () => {
        if (destroyed) return;
        clear();
        destroyed = true;
        unsubscribe();
        unsubscribe = () => {};
        playheadElement = null;
        audioTransport = null;
        playheadRuntime = null;
        requestPlayheadFrame = null;
        cancelPlayheadFrame = null;
    };

    return Object.freeze({ render, clear, destroy });
}
