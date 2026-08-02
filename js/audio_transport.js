export const AUDIO_TRANSPORT_EVENT_TYPES = Object.freeze([
    "loadedmetadata",
    "durationchange",
    "timeupdate",
    "play",
    "pause",
    "ended",
    "emptied",
    "seeking",
    "seeked",
    "ratechange",
    "error",
]);

function normalizedDuration(mediaElement) {
    const duration = mediaElement?.duration;
    return Number.isFinite(duration) && duration > 0 ? duration : 0;
}

function transportSnapshot(mediaElement) {
    const duration = normalizedDuration(mediaElement);
    const rawCurrentTime = mediaElement?.currentTime;
    const normalizedCurrentTime = Number.isFinite(rawCurrentTime) && rawCurrentTime >= 0
        ? rawCurrentTime
        : 0;
    const ended = Boolean(mediaElement?.ended);
    const playbackRate = mediaElement?.playbackRate;
    return Object.freeze({
        currentTime: duration > 0
            ? Math.min(normalizedCurrentTime, duration)
            : normalizedCurrentTime,
        duration,
        playing: mediaElement?.paused === false && !ended,
        ended,
        seeking: Boolean(mediaElement?.seeking),
        playbackRate: Number.isFinite(playbackRate) && playbackRate > 0
            ? playbackRate
            : 1,
    });
}

export function createAudioTransport(audioElement) {
    if (
        !audioElement
        || typeof audioElement.addEventListener !== "function"
        || typeof audioElement.removeEventListener !== "function"
    ) {
        throw new TypeError("createAudioTransport requires a media element with event listeners");
    }

    let mediaElement = audioElement;
    let destroyed = false;
    const subscriptions = new Set();

    const emitSnapshot = () => {
        if (destroyed || !mediaElement) return;
        const snapshot = transportSnapshot(mediaElement);
        for (const subscription of [...subscriptions]) {
            try {
                subscription.listener(snapshot);
            } catch {
                // Subscriber failures are isolated from the transport and each other.
            }
        }
    };

    for (const eventType of AUDIO_TRANSPORT_EVENT_TYPES) {
        mediaElement.addEventListener(eventType, emitSnapshot);
    }

    const getCurrentTime = () => {
        if (destroyed || !mediaElement) return 0;
        return transportSnapshot(mediaElement).currentTime;
    };

    const getDuration = () => {
        if (destroyed || !mediaElement) return 0;
        return normalizedDuration(mediaElement);
    };

    const isPlaying = () => {
        if (destroyed || !mediaElement) return false;
        return transportSnapshot(mediaElement).playing;
    };

    const seek = (seconds) => {
        if (
            destroyed
            || !mediaElement
            || typeof seconds !== "number"
            || !Number.isFinite(seconds)
        ) {
            return false;
        }
        const duration = normalizedDuration(mediaElement);
        if (!(duration > 0)) return false;
        const target = Math.min(Math.max(seconds, 0), duration);
        try {
            mediaElement.currentTime = target;
        } catch {
            return false;
        }
        emitSnapshot();
        return true;
    };

    const subscribe = (listener) => {
        if (typeof listener !== "function") {
            throw new TypeError("Audio transport subscriber must be a function");
        }
        if (destroyed || !mediaElement) return () => {};

        const subscription = { listener };
        subscriptions.add(subscription);
        try {
            listener(transportSnapshot(mediaElement));
        } catch {
            // Initial subscriber failures follow the same isolation policy as events.
        }

        let active = true;
        return () => {
            if (!active) return;
            active = false;
            subscriptions.delete(subscription);
        };
    };

    const destroy = () => {
        if (destroyed) return;
        destroyed = true;
        for (const eventType of AUDIO_TRANSPORT_EVENT_TYPES) {
            mediaElement.removeEventListener(eventType, emitSnapshot);
        }
        subscriptions.clear();
        try {
            mediaElement.pause();
        } catch {
            // A failing native pause must not retain the removed node transport.
        }
        mediaElement = null;
    };

    return Object.freeze({
        getCurrentTime,
        getDuration,
        isPlaying,
        seek,
        subscribe,
        destroy,
    });
}
