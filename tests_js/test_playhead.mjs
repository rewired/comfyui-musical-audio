import assert from "node:assert/strict";
import test from "node:test";

import {
    createPlayheadController,
    playheadPositionPercentage,
    renderPlayheadElement,
} from "../js/playhead.js";


class FakeStyle {
    constructor() {
        this.properties = new Map();
        this.setCalls = 0;
        this.removeCalls = 0;
        this.onSet = null;
    }

    setProperty(name, value) {
        this.properties.set(name, value);
        this.setCalls += 1;
        this.onSet?.(name, value);
    }

    removeProperty(name) {
        const previous = this.properties.get(name) ?? "";
        this.properties.delete(name);
        this.removeCalls += 1;
        return previous;
    }

    getPropertyValue(name) {
        return this.properties.get(name) ?? "";
    }
}


class FakeClassList {
    constructor() {
        this.values = new Set();
    }

    add(name) {
        this.values.add(name);
    }

    remove(name) {
        this.values.delete(name);
    }

    contains(name) {
        return this.values.has(name);
    }
}


class FakeElement {
    constructor() {
        this.style = new FakeStyle();
        this.classList = new FakeClassList();
    }
}


class FakeTransport {
    constructor(overrides = {}) {
        this.state = {
            currentTime: 0,
            duration: 100,
            playing: false,
            ended: false,
            seeking: false,
            playbackRate: 1,
            ...overrides,
        };
        this.listeners = new Set();
        this.subscribeCalls = 0;
        this.unsubscribeCalls = 0;
        this.destroyCalls = 0;
    }

    snapshot() {
        return Object.freeze({ ...this.state });
    }

    getCurrentTime() {
        return this.state.currentTime;
    }

    getDuration() {
        return this.state.duration;
    }

    isPlaying() {
        return this.state.playing;
    }

    subscribe(listener) {
        this.subscribeCalls += 1;
        this.listeners.add(listener);
        listener(this.snapshot());
        let active = true;
        return () => {
            if (!active) return;
            active = false;
            this.unsubscribeCalls += 1;
            this.listeners.delete(listener);
        };
    }

    emit(overrides = {}) {
        Object.assign(this.state, overrides);
        const snapshot = this.snapshot();
        for (const listener of [...this.listeners]) listener(snapshot);
    }

    destroy() {
        this.destroyCalls += 1;
    }
}


function createFrameQueue() {
    let nextId = 1;
    const callbacks = new Map();
    const cancelled = [];
    return {
        requestFrame(callback) {
            const id = nextId;
            nextId += 1;
            callbacks.set(id, callback);
            return id;
        },
        cancelFrame(id) {
            cancelled.push(id);
            callbacks.delete(id);
        },
        runNext() {
            const entry = callbacks.entries().next().value;
            if (!entry) return null;
            const [id, callback] = entry;
            callbacks.delete(id);
            callback(16.67);
            return id;
        },
        get size() {
            return callbacks.size;
        },
        get ids() {
            return [...callbacks.keys()];
        },
        cancelled,
    };
}


function createHarness(transportState = {}, runtimeOverrides = {}) {
    const element = new FakeElement();
    const transport = new FakeTransport(transportState);
    const runtime = {
        animationFrameId: 77,
        playheadAnimationFrameId: null,
        ...runtimeOverrides,
    };
    const frames = createFrameQueue();
    const controller = createPlayheadController({
        element,
        transport,
        runtime,
        requestFrame: frames.requestFrame,
        cancelFrame: frames.cancelFrame,
    });
    return { controller, element, frames, runtime, transport };
}


test("invalid duration returns null", () => {
    assert.equal(playheadPositionPercentage(5, "10"), null);
});

test("zero duration returns null", () => {
    assert.equal(playheadPositionPercentage(5, 0), null);
});

test("negative duration returns null", () => {
    assert.equal(playheadPositionPercentage(5, -10), null);
});

test("NaN duration returns null", () => {
    assert.equal(playheadPositionPercentage(5, Number.NaN), null);
});

test("beginning returns exactly zero", () => {
    assert.equal(playheadPositionPercentage(0, 100), 0);
});

test("middle returns the expected percentage", () => {
    assert.equal(playheadPositionPercentage(25, 100), 25);
});

test("end returns exactly one hundred", () => {
    assert.equal(playheadPositionPercentage(100, 100), 100);
});

test("negative time clamps to zero", () => {
    assert.equal(playheadPositionPercentage(-5, 100), 0);
});

test("time beyond duration clamps to one hundred", () => {
    assert.equal(playheadPositionPercentage(150, 100), 100);
});

test("invalid current time renders at zero for valid duration", () => {
    assert.equal(playheadPositionPercentage(Number.NaN, 100), 0);
});

test("position helper never returns NaN or Infinity", () => {
    for (const currentTime of [Number.NaN, Number.POSITIVE_INFINITY, -100, 0, 100, 1_000]) {
        const result = playheadPositionPercentage(currentTime, 100);
        assert.equal(Number.isFinite(result), true);
        assert.equal(result >= 0 && result <= 100, true);
    }
});

test("valid duration adds the visible class", () => {
    const element = new FakeElement();
    renderPlayheadElement(element, { currentTime: 0, duration: 100, playing: false });
    assert.equal(element.classList.contains("is-visible"), true);
});

test("valid duration sets the playhead position property", () => {
    const element = new FakeElement();
    renderPlayheadElement(element, { currentTime: 25, duration: 100 });
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "25%");
});

test("paused snapshot remains visible", () => {
    const element = new FakeElement();
    renderPlayheadElement(element, { currentTime: 50, duration: 100, playing: false });
    assert.equal(element.classList.contains("is-visible"), true);
});

test("invalid duration hides the element", () => {
    const element = new FakeElement();
    element.classList.add("is-visible");
    renderPlayheadElement(element, { currentTime: 50, duration: 0 });
    assert.equal(element.classList.contains("is-visible"), false);
});

test("hidden render removes stale position", () => {
    const element = new FakeElement();
    element.style.setProperty("--mau-playhead-position", "75%");
    renderPlayheadElement(element, { currentTime: 0, duration: 0 });
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "");
});

test("rendering does not mutate the snapshot", () => {
    const element = new FakeElement();
    const snapshot = Object.freeze({ currentTime: 25, duration: 100, playing: false });
    const before = { ...snapshot };
    renderPlayheadElement(element, snapshot);
    assert.deepEqual(snapshot, before);
});

test("render return value matches the rendered percentage", () => {
    const element = new FakeElement();
    const result = renderPlayheadElement(element, { currentTime: 12.5, duration: 50 });
    assert.equal(result, 25);
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "25%");
});

test("controller subscribes exactly once", () => {
    const { transport } = createHarness();
    assert.equal(transport.subscribeCalls, 1);
});

test("immediate subscription renders once", () => {
    const { element } = createHarness({ currentTime: 20 });
    assert.equal(element.style.setCalls, 1);
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "20%");
});

test("paused state queues no animation frame", () => {
    const { frames, runtime } = createHarness({ playing: false });
    assert.equal(frames.size, 0);
    assert.equal(runtime.playheadAnimationFrameId, null);
});

test("playing state queues exactly one animation frame", () => {
    const { frames } = createHarness({ playing: true });
    assert.equal(frames.size, 1);
});

test("repeated playing notifications do not queue duplicate frames", () => {
    const { frames, transport } = createHarness({ playing: true });
    transport.emit({ playing: true, currentTime: 1 });
    transport.emit({ playing: true, currentTime: 2 });
    assert.equal(frames.size, 1);
});

test("animation frame handle is stored in the dedicated runtime field", () => {
    const { frames, runtime } = createHarness({ playing: true });
    assert.deepEqual(frames.ids, [runtime.playheadAnimationFrameId]);
});

test("waveform animation frame field is never changed", () => {
    const { frames, runtime, transport } = createHarness({ playing: true });
    frames.runNext();
    transport.emit({ playing: false });
    assert.equal(runtime.animationFrameId, 77);
});

test("frame callback clears the playhead handle before rendering", () => {
    const { element, frames, runtime } = createHarness({ playing: true });
    let handleDuringRender = "not-rendered";
    element.style.onSet = () => { handleDuringRender = runtime.playheadAnimationFrameId; };
    frames.runNext();
    assert.equal(handleDuringRender, null);
});

test("active playback schedules the next frame", () => {
    const { frames, runtime } = createHarness({ playing: true });
    const firstHandle = runtime.playheadAnimationFrameId;
    frames.runNext();
    assert.equal(frames.size, 1);
    assert.notEqual(runtime.playheadAnimationFrameId, firstHandle);
});

test("paused playback stops scheduling after the active frame", () => {
    const { frames, runtime, transport } = createHarness({ playing: true });
    transport.state.playing = false;
    frames.runNext();
    assert.equal(frames.size, 0);
    assert.equal(runtime.playheadAnimationFrameId, null);
});

test("pause notification cancels a queued frame", () => {
    const { frames, runtime, transport } = createHarness({ playing: true });
    const handle = runtime.playheadAnimationFrameId;
    transport.emit({ playing: false });
    assert.deepEqual(frames.cancelled, [handle]);
    assert.equal(runtime.playheadAnimationFrameId, null);
});

test("ended state stops scheduling", () => {
    const { frames, runtime, transport } = createHarness({ playing: true });
    transport.emit({ playing: false, ended: true });
    assert.equal(frames.size, 0);
    assert.equal(runtime.playheadAnimationFrameId, null);
});

test("seeking snapshot renders the current position", () => {
    const { element, transport } = createHarness();
    transport.emit({ currentTime: 40, seeking: true });
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "40%");
});

test("programmatic seek snapshot renders immediately", () => {
    const { element, transport } = createHarness();
    transport.emit({ currentTime: 65 });
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "65%");
});

test("clear cancels a queued frame", () => {
    const { controller, frames, runtime } = createHarness({ playing: true });
    const handle = runtime.playheadAnimationFrameId;
    controller.clear();
    assert.deepEqual(frames.cancelled, [handle]);
    assert.equal(runtime.playheadAnimationFrameId, null);
});

test("clear hides the element and removes stale position", () => {
    const { controller, element } = createHarness({ currentTime: 25 });
    controller.clear();
    assert.equal(element.classList.contains("is-visible"), false);
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "");
});

test("clear remains reusable", () => {
    const { controller, element, transport } = createHarness({ currentTime: 25 });
    controller.clear();
    transport.emit({ currentTime: 50, duration: 100, playing: false });
    assert.equal(element.classList.contains("is-visible"), true);
    assert.equal(element.style.getPropertyValue("--mau-playhead-position"), "50%");
});

test("destroy cancels a queued frame", () => {
    const { controller, frames, runtime } = createHarness({ playing: true });
    const handle = runtime.playheadAnimationFrameId;
    controller.destroy();
    assert.deepEqual(frames.cancelled, [handle]);
});

test("destroy unsubscribes from the transport", () => {
    const { controller, transport } = createHarness();
    controller.destroy();
    assert.equal(transport.unsubscribeCalls, 1);
    assert.equal(transport.listeners.size, 0);
});

test("destroy hides the element", () => {
    const { controller, element } = createHarness();
    controller.destroy();
    assert.equal(element.classList.contains("is-visible"), false);
});

test("destroy releases controller references", () => {
    const { controller, transport } = createHarness();
    controller.destroy();
    transport.getCurrentTime = () => { throw new Error("retained transport"); };
    assert.equal(controller.render(), null);
});

test("destroy is idempotent", () => {
    const { controller, transport } = createHarness();
    assert.doesNotThrow(() => {
        controller.destroy();
        controller.destroy();
    });
    assert.equal(transport.unsubscribeCalls, 1);
});

test("no callback renders after destroy", () => {
    const { controller, element, transport } = createHarness();
    controller.destroy();
    const setCalls = element.style.setCalls;
    transport.emit({ currentTime: 75 });
    assert.equal(element.style.setCalls, setCalls);
});

test("two controllers remain independent", () => {
    const first = createHarness({ currentTime: 10 });
    const second = createHarness({ currentTime: 20 });
    first.transport.emit({ currentTime: 30 });
    assert.equal(first.element.style.getPropertyValue("--mau-playhead-position"), "30%");
    assert.equal(second.element.style.getPropertyValue("--mau-playhead-position"), "20%");
    first.controller.destroy();
    assert.equal(second.element.classList.contains("is-visible"), true);
});

test("controller does not destroy the transport", () => {
    const { controller, transport } = createHarness();
    controller.destroy();
    assert.equal(transport.destroyCalls, 0);
});

test("controller never touches waveform runtime animationFrameId", () => {
    const { controller, frames, runtime, transport } = createHarness({ playing: true });
    frames.runNext();
    transport.emit({ playing: false });
    controller.clear();
    controller.destroy();
    assert.equal(runtime.animationFrameId, 77);
});
