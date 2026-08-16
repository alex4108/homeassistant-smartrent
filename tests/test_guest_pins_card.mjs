import fs from "node:fs";
import vm from "node:vm";

class Element {
  constructor() {
    this.shadowRoot = null;
  }
  attachShadow() {
    const elements = new Map();
    const root = {
      innerHTML: "",
      querySelector(selector) {
        if (!elements.has(selector)) {
          elements.set(selector, {
            value: "",
            hidden: false,
            dataset: {},
            addEventListener() {},
          });
        }
        return elements.get(selector);
      },
      querySelectorAll() {
        return [];
      },
    };
    this.shadowRoot = root;
    return root;
  }
  dispatchEvent() {}
}

const registry = new Map();
const context = vm.createContext({
  HTMLElement: Element,
  CustomEvent: class {},
  customElements: {
    define(name, klass) {
      if (registry.has(name)) throw new Error(`duplicate ${name}`);
      registry.set(name, klass);
    },
    get(name) {
      return registry.get(name);
    },
  },
  window: { customCards: [] },
  navigator: { clipboard: { async writeText() {} } },
  confirm: () => false,
  console,
  Date,
  setTimeout,
  clearTimeout,
});

const source = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(source, context, { filename: process.argv[2] });
const Card = registry.get("smartrent-guest-pins-card");
if (!Card) throw new Error("card was not registered");
if (context.window.customCards.filter((card) => card.type === "smartrent-guest-pins-card").length !== 1) {
  throw new Error("card metadata was duplicated");
}

const calls = [];
const card = new Card();
card.setConfig({ title: "Guest PINs" });
card._hass = {
  async callWS(message) {
    calls.push(message);
    if (message.service === "get_guest_codes") {
      return {
        response: {
          "lock.lock": {
            guest_codes: [],
            config: {
              max_permanent_codes: 0,
              max_temporary_codes: 10,
              max_temporary_hours: 48,
              max_recurring_codes: 10,
              max_recurring_days: 5,
              max_recurring_window_hours: 2,
            },
          },
        },
      };
    }
    throw new Error(`unexpected service ${message.service}`);
  },
};
await card._refresh();
if (!card.shadowRoot.innerHTML.includes("Create guest PIN")) throw new Error("create form missing");
if (!card.shadowRoot.innerHTML.includes("No guest PINs")) throw new Error("empty state missing");
if (card.shadowRoot.innerHTML.includes('value="permanent"')) throw new Error("disabled permanent option shown");
if (calls.length !== 1 || calls[0].return_response !== true) throw new Error("response service call invalid");
if (calls[0].target?.entity_id !== "lock.lock") throw new Error("wrong lock target");
console.log(JSON.stringify({ registered: true, rendered: true, serviceCall: true, htmlBytes: card.shadowRoot.innerHTML.length }));
