import assert from "node:assert/strict";
import test from "node:test";

import {
    AUDIO_TRANSPORT_EVENT_TYPES,
    createAudioTransport,
} from "../js/audio_transport.js";


const REQUIRED_EVENTS = [
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
];


class FakeMediaElement {
    constructor(overrides = {}) {
        this.currentTime = 0;
        this.duration = 100;
        this.paused = true;
        this.ended = false;
        this.seeking = false;
        this.playbackRate = 1;
        this.pauseCalls = 0;
        this.playCalls = 0;
        this.listeners = new Map();
        Object.assign(this, overrides);
    }

    addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(listener);
    }

    removeEventListener(type, listener) {
        this.listeners.get(type)?.delete(listener);
    }

    dispatch(type) {
        const event = { type, target: this };
        for (const listener of [...(this.listeners.get(type) || [])]) listener(event);
        return event;
    }

    pause() {
        this.pauseCalls += 1;
        this.paused = true;
    }

    play() {
        this.playCalls += 1;
        this.paused = false;
    }

    listenerCount() {
        return [...this.listeners.values()].reduce((count, listeners) => count + listeners.size, 0);
    }
}


function snapshotFor(overrides = {}) {
    const media = new FakeMediaElement(overrides);
    const transport = createAudioTransport(media);
    let snapshot;
    transport.subscribe((value) => { snapshot = value; });
    return { media, snapshot, transport };
}


test("rejects a missing media element", () => {
    assert.throws(() => createAudioTransport(), TypeError);
});

test("rejects an object without event-listener methods", () => {
    assert.throws(() => createAudioTransport({ addEventListener() {} }), TypeError);
});

test("exposes exactly the intended facade methods", () => {
    const transport = createAudioTransport(new FakeMediaElement());
    assert.deepEqual(Object.keys(transport).sort(), [
        "destroy",
        "getCurrentTime",
        "getDuration",
        "isPlaying",
        "seek",
        "subscribe",
    ]);
});

test("does not expose the raw element", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    assert.equal(Object.values(transport).includes(media), false);
    assert.equal("audioElement" in transport, false);
});

test("event type list is frozen", () => {
    assert.equal(Object.isFrozen(AUDIO_TRANSPORT_EVENT_TYPES), true);
});

test("event type list contains the required exact events", () => {
    assert.deepEqual(AUDIO_TRANSPORT_EVENT_TYPES, REQUIRED_EVENTS);
});

test("subscribe emits an immediate snapshot", () => {
    const transport = createAudioTransport(new FakeMediaElement());
    let calls = 0;
    transport.subscribe(() => { calls += 1; });
    assert.equal(calls, 1);
});

test("emitted snapshots are frozen", () => {
    const { snapshot } = snapshotFor();
    assert.equal(Object.isFrozen(snapshot), true);
});

test("valid current time is preserved", () => {
    assert.equal(snapshotFor({ currentTime: 25 }).snapshot.currentTime, 25);
});

test("negative current time normalizes to zero", () => {
    assert.equal(snapshotFor({ currentTime: -5 }).snapshot.currentTime, 0);
});

test("current time beyond duration clamps to duration", () => {
    assert.equal(snapshotFor({ currentTime: 150, duration: 100 }).snapshot.currentTime, 100);
});

test("invalid current time normalizes to zero", () => {
    assert.equal(snapshotFor({ currentTime: Number.NaN }).snapshot.currentTime, 0);
});

test("valid duration is preserved", () => {
    assert.equal(snapshotFor({ duration: 42.5 }).snapshot.duration, 42.5);
});

test("NaN duration normalizes to zero", () => {
    assert.equal(snapshotFor({ duration: Number.NaN }).snapshot.duration, 0);
});

test("infinite duration normalizes to zero", () => {
    assert.equal(snapshotFor({ duration: Number.POSITIVE_INFINITY }).snapshot.duration, 0);
});

test("negative duration normalizes to zero", () => {
    assert.equal(snapshotFor({ duration: -1 }).snapshot.duration, 0);
});

test("playing is true only while not paused and not ended", () => {
    assert.equal(snapshotFor({ paused: false, ended: false }).snapshot.playing, true);
    assert.equal(snapshotFor({ paused: true, ended: false }).snapshot.playing, false);
    assert.equal(snapshotFor({ paused: false, ended: true }).snapshot.playing, false);
});

test("ended is Boolean-normalized", () => {
    assert.equal(snapshotFor({ ended: 1 }).snapshot.ended, true);
    assert.equal(snapshotFor({ ended: 0 }).snapshot.ended, false);
});

test("seeking is Boolean-normalized", () => {
    assert.equal(snapshotFor({ seeking: "yes" }).snapshot.seeking, true);
    assert.equal(snapshotFor({ seeking: "" }).snapshot.seeking, false);
});

test("invalid playback rate becomes one", () => {
    assert.equal(snapshotFor({ playbackRate: 0 }).snapshot.playbackRate, 1);
    assert.equal(snapshotFor({ playbackRate: Number.NaN }).snapshot.playbackRate, 1);
});

test("valid playback rate is preserved", () => {
    assert.equal(snapshotFor({ playbackRate: 1.5 }).snapshot.playbackRate, 1.5);
});

test("every required event emits a snapshot", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    let calls = 0;
    transport.subscribe(() => { calls += 1; });
    for (const eventType of REQUIRED_EVENTS) media.dispatch(eventType);
    assert.equal(calls, REQUIRED_EVENTS.length + 1);
});

test("unsubscribe prevents future notifications", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    let calls = 0;
    const unsubscribe = transport.subscribe(() => { calls += 1; });
    unsubscribe();
    media.dispatch("timeupdate");
    assert.equal(calls, 1);
});

test("unsubscribe is idempotent", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    const unsubscribe = transport.subscribe(() => {});
    assert.doesNotThrow(() => {
        unsubscribe();
        unsubscribe();
    });
});

test("one throwing subscriber does not block another", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    transport.subscribe(() => { throw new Error("subscriber failed"); });
    let calls = 0;
    transport.subscribe(() => { calls += 1; });
    media.dispatch("timeupdate");
    assert.equal(calls, 2);
});

test("subscribers never receive the raw event", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    let args;
    transport.subscribe((...values) => { args = values; });
    const event = media.dispatch("seeking");
    assert.equal(args.length, 1);
    assert.notEqual(args[0], event);
});

test("subscribers never receive the raw element", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    let snapshot;
    transport.subscribe((value) => { snapshot = value; });
    assert.notEqual(snapshot, media);
    assert.equal(Object.values(snapshot).includes(media), false);
});

test("destroyed transport does not emit to new subscribers", () => {
    const transport = createAudioTransport(new FakeMediaElement());
    transport.destroy();
    let calls = 0;
    transport.subscribe(() => { calls += 1; });
    assert.equal(calls, 0);
});

test("negative seek clamps to zero", () => {
    const media = new FakeMediaElement();
    createAudioTransport(media).seek(-5);
    assert.equal(media.currentTime, 0);
});

test("in-range seek is exact", () => {
    const media = new FakeMediaElement();
    createAudioTransport(media).seek(25);
    assert.equal(media.currentTime, 25);
});

test("oversized seek clamps to duration", () => {
    const media = new FakeMediaElement();
    createAudioTransport(media).seek(150);
    assert.equal(media.currentTime, 100);
});

test("NaN seek returns false", () => {
    assert.equal(createAudioTransport(new FakeMediaElement()).seek(Number.NaN), false);
});

test("infinite seek returns false", () => {
    assert.equal(createAudioTransport(new FakeMediaElement()).seek(Number.POSITIVE_INFINITY), false);
});

test("string seek returns false", () => {
    assert.equal(createAudioTransport(new FakeMediaElement()).seek("25"), false);
});

test("seek without valid duration returns false", () => {
    assert.equal(createAudioTransport(new FakeMediaElement({ duration: 0 })).seek(25), false);
});

test("native current-time assignment failure returns false", () => {
    const media = new FakeMediaElement();
    Object.defineProperty(media, "currentTime", {
        configurable: true,
        get: () => 0,
        set: () => { throw new Error("not seekable"); },
    });
    assert.equal(createAudioTransport(media).seek(25), false);
});

test("successful seek returns true", () => {
    assert.equal(createAudioTransport(new FakeMediaElement()).seek(25), true);
});

test("successful seek emits immediately", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    const times = [];
    transport.subscribe((snapshot) => times.push(snapshot.currentTime));
    transport.seek(25);
    assert.deepEqual(times, [0, 25]);
});

test("seek does not call play", () => {
    const media = new FakeMediaElement();
    createAudioTransport(media).seek(25);
    assert.equal(media.playCalls, 0);
});

test("seek does not call pause", () => {
    const media = new FakeMediaElement();
    createAudioTransport(media).seek(25);
    assert.equal(media.pauseCalls, 0);
});

test("destroy removes all installed event listeners", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    assert.equal(media.listenerCount(), REQUIRED_EVENTS.length);
    transport.destroy();
    assert.equal(media.listenerCount(), 0);
});

test("destroy clears subscribers", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    let calls = 0;
    transport.subscribe(() => { calls += 1; });
    transport.destroy();
    media.dispatch("timeupdate");
    assert.equal(calls, 1);
});

test("destroy pauses native playback", () => {
    const media = new FakeMediaElement({ paused: false });
    createAudioTransport(media).destroy();
    assert.equal(media.pauseCalls, 1);
    assert.equal(media.paused, true);
});

test("destroy releases the media element", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    transport.destroy();
    Object.defineProperty(media, "duration", { get: () => { throw new Error("retained"); } });
    assert.equal(transport.getDuration(), 0);
});

test("destroy is idempotent", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    transport.destroy();
    transport.destroy();
    assert.equal(media.pauseCalls, 1);
});

test("getters return safe defaults after destroy", () => {
    const transport = createAudioTransport(new FakeMediaElement({ paused: false }));
    transport.destroy();
    assert.equal(transport.getCurrentTime(), 0);
    assert.equal(transport.getDuration(), 0);
    assert.equal(transport.isPlaying(), false);
});

test("seek returns false after destroy", () => {
    const transport = createAudioTransport(new FakeMediaElement());
    transport.destroy();
    assert.equal(transport.seek(25), false);
});

test("subscribe is a no-op after destroy", () => {
    const transport = createAudioTransport(new FakeMediaElement());
    transport.destroy();
    const unsubscribe = transport.subscribe(() => assert.fail("unexpected callback"));
    assert.equal(typeof unsubscribe, "function");
    assert.doesNotThrow(unsubscribe);
});

test("one transport does not affect another", () => {
    const firstMedia = new FakeMediaElement();
    const secondMedia = new FakeMediaElement();
    const first = createAudioTransport(firstMedia);
    const second = createAudioTransport(secondMedia);
    let secondCalls = 0;
    second.subscribe(() => { secondCalls += 1; });
    first.destroy();
    secondMedia.dispatch("timeupdate");
    assert.equal(secondCalls, 2);
    assert.equal(second.getDuration(), 100);
});

test("duplicate listener subscriptions are independent", () => {
    const media = new FakeMediaElement();
    const transport = createAudioTransport(media);
    let calls = 0;
    const listener = () => { calls += 1; };
    const unsubscribeFirst = transport.subscribe(listener);
    const unsubscribeSecond = transport.subscribe(listener);
    assert.equal(calls, 2);
    unsubscribeFirst();
    media.dispatch("timeupdate");
    assert.equal(calls, 3);
    unsubscribeSecond();
    media.dispatch("timeupdate");
    assert.equal(calls, 3);
});
