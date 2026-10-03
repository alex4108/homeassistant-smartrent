"""Passive pre-manager Sophie receiver evidence; never changes scanning or presence.

Call install(hass) from the owning integration on HA's event loop. This samples
existing scanner caches, not advertisements subscriptions, and emits only target
metadata plus aggregate peer counts. The owning collector must TTL retained rows.
"""
import math
import time

EVENT = 'gatekeeper_ble_reception'
PERIOD = 1.0
TARGET = 'DD:88:00:00:1E:3E'
BEACON = bytes.fromhex('0215426c7565436861726d426561636f6e730efe1355')
# Explicit source allowlist; physical location is registry/user evidence, not RSSI.
NAMES = ('bedroomchip', 'guestchip', 'commonchip')
TTL_SECONDS = 7 * 86400
MAX_PEERS = 4096


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Sampler:
    """Bounded metadata projection; never exports peer addresses or payloads."""
    def __init__(self):
        self.previous = {}
        self.last_target = {}
        self.last_poll = None
        self.last_health = None

    def sample(self, scanners, mono, wall):
        output = []
        gap = self.last_poll is not None and mono - self.last_poll > 3 * PERIOD
        self.last_poll = mono
        health_due = self.last_health is None or mono - self.last_health >= 30
        active = set()
        for scanner in list(scanners)[:16]:
            name = str(scanner.name).lower()
            if not any(n in name for n in NAMES):
                continue
            source = str(scanner.source)
            active.add(source)
            stamps = scanner.discovered_device_timestamps
            if len(stamps) > MAX_PEERS:
                output.append({'kind': 'health', 'receiver': source, 'reason': 'peer_cap'})
                continue
            old = self.previous.get(source, {})
            baseline = source not in self.previous
            advanced = sum(finite(s) and s > old.get(a, s) for a, s in stamps.items())
            # This is the only retained household cache: bounded timestamps in RAM,
            # overwritten each poll, discarded on unload; never persisted/emitted.
            self.previous[source] = dict(stamps)
            matches = []
            for address, stamp in stamps.items():
                if not finite(stamp) or stamp > mono + 1 or mono - stamp > 600:
                    continue
                value = scanner.get_discovered_device_advertisement_data(address)
                if value is None:
                    continue
                adv = value[1]
                exact = address.upper() == TARGET
                tuple_match = any(isinstance(v, bytes) and v.startswith(BEACON)
                                  for k, v in adv.manufacturer_data.items() if k == 76)
                if not exact and not tuple_match:
                    continue
                # Tuple on another address is identity ambiguity, never authorizes.
                identity = 'fixed_mac' if exact else 'beacon_tuple_other_address'
                matches.append((stamp, adv.rssi, identity))
            # Prefer the exact fixed identity if a colliding tuple exists elsewhere.
            exact_matches = [m for m in matches if m[2] == 'fixed_mac']
            latest = max(exact_matches or matches, default=None)
            prior = self.last_target.get(source)
            if latest is not None:
                stamp, rssi, identity = latest
                is_new = prior is None or stamp > prior
                if is_new:
                    self.last_target[source] = stamp
                    output.append({'kind': 'target', 'receiver': source,
                                   'receiver_name': next(n for n in NAMES if n in name),
                                   'target': 'sophie', 'identity': identity,
                                   'host_monotonic': stamp, 'host_utc_approx': wall - (mono - stamp),
                                   'observed_utc': wall, 'age_seconds': max(0, mono - stamp),
                                   'rssi': rssi if finite(rssi) else None,
                                   'baseline': baseline,
                                   'first_seen_in_capture': prior is None and not baseline,
                                   'sample_gap': gap,
                                   'sampled_interarrival_seconds': stamp - prior if prior is not None else None,
                                   'cache_return_after_30s': prior is not None and stamp - prior >= 30,
                                   'matching_addresses': len(matches)})
            if health_due or gap or baseline:
                valid_stamps = [s for s in stamps.values() if finite(s) and s <= mono + 1]
                output.append({'kind': 'health', 'receiver': source,
                               'receiver_name': next(n for n in NAMES if n in name),
                               'observed_utc': wall, 'scanning': bool(scanner.scanning),
                               'known_devices': len(stamps), 'peer_timestamp_advances': advanced,
                               'latest_any_age': max(0, mono - max(valid_stamps)) if valid_stamps else None,
                               'sample_gap': gap, 'baseline': baseline,
                               'sample_period_seconds': PERIOD,
                               'measurement': 'sampled_host_ingestion_not_rf_or_packet_count'})
        for source in set(self.previous) - active:
            output.append({'kind': 'health', 'receiver': source, 'observed_utc': wall,
                           'reason': 'scanner_unregistered'})
            self.previous.pop(source, None)
            self.last_target.pop(source, None)
        if health_due:
            self.last_health = mono
        return output


def install(hass):
    """Attach timer only. Returns idempotent unload; no I/O in sampled callback."""
    from homeassistant.components.bluetooth import async_current_scanners
    from homeassistant.core import callback
    from homeassistant.helpers.event import async_track_time_interval
    from datetime import timedelta
    from bluetooth_data_tools import monotonic_time_coarse

    key = EVENT + '_unload'
    if key in hass.data:
        return hass.data[key]
    sampler = Sampler()
    error_reported = False

    @callback
    def tick(_now):
        nonlocal error_reported
        try:
            rows = sampler.sample(async_current_scanners(hass), monotonic_time_coarse(), time.time())
            for row in rows:
                hass.bus.async_fire(EVENT, row)
            error_reported = False
        except Exception as exc:
            # Evidence must fail isolated: never propagate into presence/lock code.
            if not error_reported:
                hass.bus.async_fire(EVENT, {'kind': 'health', 'reason': 'sampler_error',
                                           'error_type': type(exc).__name__})
                error_reported = True

    remove = async_track_time_interval(hass, tick, timedelta(seconds=PERIOD))
    @callback
    def unload():
        if hass.data.get(key) is unload:
            remove()
            hass.data.pop(key, None)
            sampler.previous.clear()
            sampler.last_target.clear()
    hass.data[key] = unload
    return unload
