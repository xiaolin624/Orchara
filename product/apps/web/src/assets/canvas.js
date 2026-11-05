var OrcharaCanvasBundle = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/main.js
  var main_exports = {};
  __export(main_exports, {
    mount: () => mount
  });

  // node_modules/@vue/shared/dist/shared.esm-bundler.js
  // @__NO_SIDE_EFFECTS__
  function makeMap(str) {
    const map2 = /* @__PURE__ */ Object.create(null);
    for (const key of str.split(",")) map2[key] = 1;
    return (val) => val in map2;
  }
  var EMPTY_OBJ = true ? Object.freeze({}) : {};
  var EMPTY_ARR = true ? Object.freeze([]) : [];
  var NOOP = () => {
  };
  var NO = () => false;
  var isOn = (key) => key.charCodeAt(0) === 111 && key.charCodeAt(1) === 110 && // uppercase letter
  (key.charCodeAt(2) > 122 || key.charCodeAt(2) < 97);
  var isModelListener = (key) => key.startsWith("onUpdate:");
  var extend = Object.assign;
  var remove = (arr, el) => {
    const i = arr.indexOf(el);
    if (i > -1) {
      arr.splice(i, 1);
    }
  };
  var hasOwnProperty = Object.prototype.hasOwnProperty;
  var hasOwn = (val, key) => hasOwnProperty.call(val, key);
  var isArray = Array.isArray;
  var isMap = (val) => toTypeString(val) === "[object Map]";
  var isSet = (val) => toTypeString(val) === "[object Set]";
  var isDate = (val) => toTypeString(val) === "[object Date]";
  var isFunction = (val) => typeof val === "function";
  var isString = (val) => typeof val === "string";
  var isSymbol = (val) => typeof val === "symbol";
  var isObject = (val) => val !== null && typeof val === "object";
  var isPromise = (val) => {
    return (isObject(val) || isFunction(val)) && isFunction(val.then) && isFunction(val.catch);
  };
  var objectToString = Object.prototype.toString;
  var toTypeString = (value) => objectToString.call(value);
  var toRawType = (value) => {
    return toTypeString(value).slice(8, -1);
  };
  var isPlainObject = (val) => toTypeString(val) === "[object Object]";
  var isIntegerKey = (key) => isString(key) && key !== "NaN" && key[0] !== "-" && "" + parseInt(key, 10) === key;
  var isReservedProp = /* @__PURE__ */ makeMap(
    // the leading comma is intentional so empty string "" is also included
    ",key,ref,ref_for,ref_key,onVnodeBeforeMount,onVnodeMounted,onVnodeBeforeUpdate,onVnodeUpdated,onVnodeBeforeUnmount,onVnodeUnmounted"
  );
  var isBuiltInDirective = /* @__PURE__ */ makeMap(
    "bind,cloak,else-if,else,for,html,if,model,on,once,pre,show,slot,text,memo"
  );
  var cacheStringFunction = (fn) => {
    const cache = /* @__PURE__ */ Object.create(null);
    return ((str) => {
      const hit = cache[str];
      return hit || (cache[str] = fn(str));
    });
  };
  var camelizeRE = /-\w/g;
  var camelize = cacheStringFunction(
    (str) => {
      return str.replace(camelizeRE, (c) => c.slice(1).toUpperCase());
    }
  );
  var hyphenateRE = /\B([A-Z])/g;
  var hyphenate = cacheStringFunction(
    (str) => str.replace(hyphenateRE, "-$1").toLowerCase()
  );
  var capitalize = cacheStringFunction((str) => {
    return str.charAt(0).toUpperCase() + str.slice(1);
  });
  var toHandlerKey = cacheStringFunction(
    (str) => {
      const s = str ? `on${capitalize(str)}` : ``;
      return s;
    }
  );
  var hasChanged = (value, oldValue) => !Object.is(value, oldValue);
  var invokeArrayFns = (fns, ...arg) => {
    for (let i = 0; i < fns.length; i++) {
      fns[i](...arg);
    }
  };
  var def = (obj, key, value, writable = false) => {
    Object.defineProperty(obj, key, {
      configurable: true,
      enumerable: false,
      writable,
      value
    });
  };
  var looseToNumber = (val) => {
    const n = parseFloat(val);
    return isNaN(n) ? val : n;
  };
  var _globalThis;
  var getGlobalThis = () => {
    return _globalThis || (_globalThis = typeof globalThis !== "undefined" ? globalThis : typeof self !== "undefined" ? self : typeof window !== "undefined" ? window : typeof global !== "undefined" ? global : {});
  };
  function normalizeStyle(value) {
    if (isArray(value)) {
      const res = {};
      for (let i = 0; i < value.length; i++) {
        const item = value[i];
        const normalized = isString(item) ? parseStringStyle(item) : normalizeStyle(item);
        if (normalized) {
          for (const key in normalized) {
            res[key] = normalized[key];
          }
        }
      }
      return res;
    } else if (isString(value) || isObject(value)) {
      return value;
    }
  }
  var listDelimiterRE = /;(?![^(]*\))/g;
  var propertyDelimiterRE = /:([^]+)/;
  var styleCommentRE = /"(?:[^"\\]|\\[^])*"|'(?:[^'\\]|\\[^])*'|\\[^]|\/\*[^]*?\*\//g;
  function parseStringStyle(cssText) {
    const ret = {};
    cssText.replace(styleCommentRE, (match) => match.startsWith("/*") ? "" : match).split(listDelimiterRE).forEach((item) => {
      if (item) {
        const tmp = item.split(propertyDelimiterRE);
        tmp.length > 1 && (ret[tmp[0].trim()] = tmp[1].trim());
      }
    });
    return ret;
  }
  function normalizeClass(value) {
    let res = "";
    if (isString(value)) {
      res = value;
    } else if (isArray(value)) {
      for (let i = 0; i < value.length; i++) {
        const normalized = normalizeClass(value[i]);
        if (normalized) {
          res += normalized + " ";
        }
      }
    } else if (isObject(value)) {
      for (const name in value) {
        if (value[name]) {
          res += name + " ";
        }
      }
    }
    return res.trim();
  }
  var HTML_TAGS = "html,body,base,head,link,meta,style,title,address,article,aside,footer,header,hgroup,h1,h2,h3,h4,h5,h6,nav,section,div,dd,dl,dt,figcaption,figure,picture,hr,img,li,main,ol,p,pre,ul,a,b,abbr,bdi,bdo,br,cite,code,data,dfn,em,i,kbd,mark,q,rp,rt,ruby,s,samp,small,span,strong,sub,sup,time,u,var,wbr,area,audio,map,track,video,embed,object,param,source,canvas,script,noscript,del,ins,caption,col,colgroup,table,thead,tbody,td,th,tr,button,datalist,fieldset,form,input,label,legend,meter,optgroup,option,output,progress,select,textarea,details,dialog,menu,summary,template,blockquote,iframe,tfoot";
  var SVG_TAGS = "svg,animate,animateMotion,animateTransform,circle,clipPath,color-profile,defs,desc,discard,ellipse,feBlend,feColorMatrix,feComponentTransfer,feComposite,feConvolveMatrix,feDiffuseLighting,feDisplacementMap,feDistantLight,feDropShadow,feFlood,feFuncA,feFuncB,feFuncG,feFuncR,feGaussianBlur,feImage,feMerge,feMergeNode,feMorphology,feOffset,fePointLight,feSpecularLighting,feSpotLight,feTile,feTurbulence,filter,foreignObject,g,hatch,hatchpath,image,line,linearGradient,marker,mask,mesh,meshgradient,meshpatch,meshrow,metadata,mpath,path,pattern,polygon,polyline,radialGradient,rect,set,solidcolor,stop,switch,symbol,text,textPath,title,tspan,unknown,use,view";
  var MATH_TAGS = "annotation,annotation-xml,maction,maligngroup,malignmark,math,menclose,merror,mfenced,mfrac,mfraction,mglyph,mi,mlabeledtr,mlongdiv,mmultiscripts,mn,mo,mover,mpadded,mphantom,mprescripts,mroot,mrow,ms,mscarries,mscarry,msgroup,msline,mspace,msqrt,msrow,mstack,mstyle,msub,msubsup,msup,mtable,mtd,mtext,mtr,munder,munderover,none,semantics";
  var isHTMLTag = /* @__PURE__ */ makeMap(HTML_TAGS);
  var isSVGTag = /* @__PURE__ */ makeMap(SVG_TAGS);
  var isMathMLTag = /* @__PURE__ */ makeMap(MATH_TAGS);
  var specialBooleanAttrs = `itemscope,allowfullscreen,formnovalidate,ismap,nomodule,novalidate,playsinline,readonly`;
  var isSpecialBooleanAttr = /* @__PURE__ */ makeMap(specialBooleanAttrs);
  var isBooleanAttr = /* @__PURE__ */ makeMap(
    specialBooleanAttrs + `,async,autofocus,autoplay,controls,default,defer,disabled,inert,loop,open,required,reversed,scoped,seamless,checked,muted,multiple,selected`
  );
  function includeBooleanAttr(value) {
    return !!value || value === "";
  }
  function looseCompareArrays(a, b, seen) {
    if (a.length !== b.length) return false;
    let equal = true;
    for (let i = 0; equal && i < a.length; i++) {
      equal = looseEqual(a[i], b[i], seen);
    }
    return equal;
  }
  function looseCompareCollections(a, b, seen) {
    if (a.size !== b.size) return false;
    const candidates = Array.from(b);
    const matched = new Uint8Array(candidates.length);
    for (const item of a) {
      let index = -1;
      for (let i = 0; i < candidates.length; i++) {
        if (!matched[i] && looseEqual(item, candidates[i], seen)) {
          index = i;
          break;
        }
      }
      if (index < 0) return false;
      matched[index] = 1;
    }
    return true;
  }
  function looseCompareObjects(a, b, seen) {
    let aValidType = isMap(a);
    let bValidType = isMap(b);
    if (aValidType || bValidType) {
      return aValidType && bValidType ? looseCompareCollections(a, b, seen) : false;
    }
    aValidType = isSet(a);
    bValidType = isSet(b);
    if (aValidType || bValidType) {
      return aValidType && bValidType ? looseCompareCollections(a, b, seen) : false;
    }
    const aKeysCount = Object.keys(a).length;
    const bKeysCount = Object.keys(b).length;
    if (aKeysCount !== bKeysCount) {
      return false;
    }
    for (const key in a) {
      const aHasKey = a.hasOwnProperty(key);
      const bHasKey = b.hasOwnProperty(key);
      if (aHasKey && !bHasKey || !aHasKey && bHasKey || !looseEqual(a[key], b[key], seen)) {
        return false;
      }
    }
    return String(a) === String(b);
  }
  function looseCompareNested(a, b, seen, compare) {
    if (!seen) {
      seen = [/* @__PURE__ */ new Map(), /* @__PURE__ */ new Map()];
    }
    const [seenA, seenB] = seen;
    if (seenA.has(a) || seenB.has(b)) {
      return seenA.get(a) === b && seenB.get(b) === a;
    }
    seenA.set(a, b);
    seenB.set(b, a);
    const equal = compare(a, b, seen);
    seenA.delete(a);
    seenB.delete(b);
    return equal;
  }
  function looseEqual(a, b, seen) {
    if (a === b) return true;
    let aValidType = isDate(a);
    let bValidType = isDate(b);
    if (aValidType || bValidType) {
      return aValidType && bValidType ? a.getTime() === b.getTime() : false;
    }
    aValidType = isSymbol(a);
    bValidType = isSymbol(b);
    if (aValidType || bValidType) {
      return a === b;
    }
    aValidType = isArray(a);
    bValidType = isArray(b);
    if (aValidType || bValidType) {
      return aValidType && bValidType ? looseCompareNested(a, b, seen, looseCompareArrays) : false;
    }
    aValidType = isObject(a);
    bValidType = isObject(b);
    if (aValidType || bValidType) {
      if (!aValidType || !bValidType) {
        return false;
      }
      return looseCompareNested(a, b, seen, looseCompareObjects);
    }
    return String(a) === String(b);
  }
  var isRef = (val) => {
    return !!(val && val["__v_isRef"] === true);
  };
  var toDisplayString = (val) => {
    return isString(val) ? val : val == null ? "" : isArray(val) || isObject(val) && (val.toString === objectToString || !isFunction(val.toString)) ? isRef(val) ? toDisplayString(val.value) : JSON.stringify(val, replacer, 2) : String(val);
  };
  var replacer = (_key, val) => {
    if (isRef(val)) {
      return replacer(_key, val.value);
    } else if (isMap(val)) {
      return {
        [`Map(${val.size})`]: [...val.entries()].reduce(
          (entries, [key, val2], i) => {
            entries[stringifySymbol(key, i) + " =>"] = val2;
            return entries;
          },
          {}
        )
      };
    } else if (isSet(val)) {
      return {
        [`Set(${val.size})`]: [...val.values()].map((v) => stringifySymbol(v))
      };
    } else if (isSymbol(val)) {
      return stringifySymbol(val);
    } else if (isObject(val) && !isArray(val) && !isPlainObject(val)) {
      return String(val);
    }
    return val;
  };
  var stringifySymbol = (v, i = "") => {
    var _a;
    return (
      // Symbol.description in es2019+ so we need to cast here to pass
      // the lib: es2016 check
      isSymbol(v) ? `Symbol(${(_a = v.description) != null ? _a : i})` : v
    );
  };

  // node_modules/@vue/reactivity/dist/reactivity.esm-bundler.js
  function warn(msg, ...args) {
    console.warn(`[Vue warn] ${msg}`, ...args);
  }
  var activeEffectScope;
  var EffectScope = class {
    // TODO isolatedDeclarations "__v_skip"
    constructor(detached = false) {
      this.detached = detached;
      this._active = true;
      this._on = 0;
      this.effects = [];
      this.cleanups = [];
      this._isPaused = false;
      this._warnOnRun = true;
      this.__v_skip = true;
      if (!detached && activeEffectScope) {
        if (activeEffectScope.active) {
          this.parent = activeEffectScope;
          this.index = (activeEffectScope.scopes || (activeEffectScope.scopes = [])).push(
            this
          ) - 1;
        } else {
          this._active = false;
          this._warnOnRun = false;
        }
      }
    }
    get active() {
      return this._active;
    }
    pause() {
      if (this._active) {
        this._isPaused = true;
        let i, l;
        if (this.scopes) {
          const scopes = this.scopes.slice();
          for (i = 0, l = scopes.length; i < l; i++) {
            scopes[i].pause();
          }
        }
        for (i = 0, l = this.effects.length; i < l; i++) {
          this.effects[i].pause();
        }
      }
    }
    /**
     * Resumes the effect scope, including all child scopes and effects.
     */
    resume() {
      if (this._active) {
        if (this._isPaused) {
          this._isPaused = false;
          let i, l;
          if (this.scopes) {
            const scopes = this.scopes.slice();
            for (i = 0, l = scopes.length; i < l; i++) {
              scopes[i].resume();
            }
          }
          const effects = this.effects.slice();
          for (i = 0, l = effects.length; i < l; i++) {
            effects[i].resume();
          }
        }
      }
    }
    run(fn) {
      if (this._active) {
        const currentEffectScope = activeEffectScope;
        try {
          activeEffectScope = this;
          return fn();
        } finally {
          activeEffectScope = currentEffectScope;
        }
      } else if (this._warnOnRun) {
        warn(`cannot run an inactive effect scope.`);
      }
    }
    /**
     * This should only be called on non-detached scopes
     * @internal
     */
    on() {
      if (++this._on === 1) {
        this.prevScope = activeEffectScope;
        activeEffectScope = this;
      }
    }
    /**
     * This should only be called on non-detached scopes
     * @internal
     */
    off() {
      if (this._on > 0 && --this._on === 0) {
        if (activeEffectScope === this) {
          activeEffectScope = this.prevScope;
        } else {
          let current = activeEffectScope;
          while (current) {
            if (current.prevScope === this) {
              current.prevScope = this.prevScope;
              break;
            }
            current = current.prevScope;
          }
        }
        this.prevScope = void 0;
      }
    }
    stop(fromParent) {
      if (this._active) {
        this._active = false;
        let i, l;
        for (i = 0, l = this.effects.length; i < l; i++) {
          this.effects[i].stop();
        }
        this.effects.length = 0;
        for (i = 0, l = this.cleanups.length; i < l; i++) {
          this.cleanups[i]();
        }
        this.cleanups.length = 0;
        if (this.scopes) {
          const scopes = this.scopes.slice();
          for (i = 0, l = scopes.length; i < l; i++) {
            scopes[i].stop(true);
          }
          this.scopes.length = 0;
        }
        if (!this.detached && this.parent && !fromParent) {
          const last = this.parent.scopes.pop();
          if (last && last !== this) {
            this.parent.scopes[this.index] = last;
            last.index = this.index;
          }
        }
        this.parent = void 0;
      }
    }
  };
  function effectScope(detached) {
    return new EffectScope(detached);
  }
  function getCurrentScope() {
    return activeEffectScope;
  }
  function onScopeDispose(fn, failSilently = false) {
    if (activeEffectScope) {
      activeEffectScope.cleanups.push(fn);
    } else if (!failSilently) {
      warn(
        `onScopeDispose() is called when there is no active effect scope to be associated with.`
      );
    }
  }
  var activeSub;
  var pausedQueueEffects = /* @__PURE__ */ new WeakSet();
  var ReactiveEffect = class {
    constructor(fn) {
      this.fn = fn;
      this.deps = void 0;
      this.depsTail = void 0;
      this.flags = 1 | 4;
      this.next = void 0;
      this.cleanup = void 0;
      this.scheduler = void 0;
      if (activeEffectScope) {
        if (activeEffectScope.active) {
          activeEffectScope.effects.push(this);
        } else {
          this.flags &= -2;
        }
      }
    }
    pause() {
      this.flags |= 64;
    }
    resume() {
      if (this.flags & 64) {
        this.flags &= -65;
        if (pausedQueueEffects.has(this)) {
          pausedQueueEffects.delete(this);
          this.trigger();
        }
      }
    }
    /**
     * @internal
     */
    notify() {
      if (this.flags & 2 && !(this.flags & 32)) {
        return;
      }
      if (!(this.flags & 8)) {
        batch(this);
      }
    }
    run() {
      if (!(this.flags & 1)) {
        return this.fn();
      }
      this.flags |= 2;
      cleanupEffect(this);
      prepareDeps(this);
      const prevEffect = activeSub;
      const prevShouldTrack = shouldTrack;
      activeSub = this;
      shouldTrack = true;
      try {
        return this.fn();
      } finally {
        if (activeSub !== this) {
          warn(
            "Active effect was not restored correctly - this is likely a Vue internal bug."
          );
        }
        cleanupDeps(this);
        activeSub = prevEffect;
        shouldTrack = prevShouldTrack;
        this.flags &= -3;
      }
    }
    stop() {
      if (this.flags & 1) {
        for (let link = this.deps; link; link = link.nextDep) {
          removeSub(link);
        }
        this.deps = this.depsTail = void 0;
        cleanupEffect(this);
        this.onStop && this.onStop();
        this.flags &= -2;
      }
    }
    trigger() {
      if (this.flags & 64) {
        pausedQueueEffects.add(this);
      } else if (this.scheduler) {
        this.scheduler();
      } else {
        this.runIfDirty();
      }
    }
    /**
     * @internal
     */
    runIfDirty() {
      if (isDirty(this)) {
        this.run();
      }
    }
    get dirty() {
      return isDirty(this);
    }
  };
  var batchDepth = 0;
  var batchedSub;
  var batchedComputed;
  function batch(sub, isComputed = false) {
    sub.flags |= 8;
    if (isComputed) {
      sub.next = batchedComputed;
      batchedComputed = sub;
      return;
    }
    sub.next = batchedSub;
    batchedSub = sub;
  }
  function startBatch() {
    batchDepth++;
  }
  function endBatch() {
    if (--batchDepth > 0) {
      return;
    }
    if (batchedComputed) {
      let e = batchedComputed;
      batchedComputed = void 0;
      while (e) {
        const next = e.next;
        e.next = void 0;
        e.flags &= -9;
        e = next;
      }
    }
    let error;
    while (batchedSub) {
      let e = batchedSub;
      batchedSub = void 0;
      while (e) {
        const next = e.next;
        e.next = void 0;
        e.flags &= -9;
        if (e.flags & 1) {
          try {
            ;
            e.trigger();
          } catch (err) {
            if (!error) error = err;
          }
        }
        e = next;
      }
    }
    if (error) throw error;
  }
  function prepareDeps(sub) {
    for (let link = sub.deps; link; link = link.nextDep) {
      link.version = -1;
      link.prevActiveLink = link.dep.activeLink;
      link.dep.activeLink = link;
    }
  }
  function cleanupDeps(sub) {
    let head;
    let tail = sub.depsTail;
    let link = tail;
    while (link) {
      const prev = link.prevDep;
      if (link.version === -1) {
        if (link === tail) tail = prev;
        removeSub(link);
        removeDep(link);
      } else {
        head = link;
      }
      link.dep.activeLink = link.prevActiveLink;
      link.prevActiveLink = void 0;
      link = prev;
    }
    sub.deps = head;
    sub.depsTail = tail;
  }
  function isDirty(sub) {
    for (let link = sub.deps; link; link = link.nextDep) {
      if (link.dep.version !== link.version || link.dep.computed && (refreshComputed(link.dep.computed) || link.dep.version !== link.version)) {
        return true;
      }
    }
    if (sub._dirty) {
      return true;
    }
    return false;
  }
  function refreshComputed(computed3) {
    if (computed3.flags & 4 && !(computed3.flags & 16)) {
      return;
    }
    computed3.flags &= -17;
    if (computed3.globalVersion === globalVersion) {
      return;
    }
    computed3.globalVersion = globalVersion;
    if (!computed3.isSSR && computed3.flags & 128 && (!computed3.deps && !computed3._dirty || !isDirty(computed3))) {
      return;
    }
    computed3.flags |= 2;
    const dep = computed3.dep;
    const prevSub = activeSub;
    const prevShouldTrack = shouldTrack;
    activeSub = computed3;
    shouldTrack = true;
    try {
      prepareDeps(computed3);
      const value = computed3.fn(computed3._value);
      if (dep.version === 0 || hasChanged(value, computed3._value)) {
        computed3.flags |= 128;
        computed3._value = value;
        dep.version++;
      }
    } catch (err) {
      dep.version++;
      throw err;
    } finally {
      activeSub = prevSub;
      shouldTrack = prevShouldTrack;
      cleanupDeps(computed3);
      computed3.flags &= -3;
    }
  }
  function removeSub(link, soft = false) {
    const { dep, prevSub, nextSub } = link;
    if (prevSub) {
      prevSub.nextSub = nextSub;
      link.prevSub = void 0;
    }
    if (nextSub) {
      nextSub.prevSub = prevSub;
      link.nextSub = void 0;
    }
    if (dep.subsHead === link) {
      dep.subsHead = nextSub;
    }
    if (dep.subs === link) {
      dep.subs = prevSub;
      if (!prevSub && dep.computed) {
        dep.computed.flags &= -5;
        for (let l = dep.computed.deps; l; l = l.nextDep) {
          removeSub(l, true);
        }
      }
    }
    if (!soft && !--dep.sc && dep.map) {
      dep.map.delete(dep.key);
    }
  }
  function removeDep(link) {
    const { prevDep, nextDep } = link;
    if (prevDep) {
      prevDep.nextDep = nextDep;
      link.prevDep = void 0;
    }
    if (nextDep) {
      nextDep.prevDep = prevDep;
      link.nextDep = void 0;
    }
  }
  var shouldTrack = true;
  var trackStack = [];
  function pauseTracking() {
    trackStack.push(shouldTrack);
    shouldTrack = false;
  }
  function resetTracking() {
    const last = trackStack.pop();
    shouldTrack = last === void 0 ? true : last;
  }
  function cleanupEffect(e) {
    const { cleanup } = e;
    e.cleanup = void 0;
    if (cleanup) {
      const prevSub = activeSub;
      activeSub = void 0;
      try {
        cleanup();
      } finally {
        activeSub = prevSub;
      }
    }
  }
  var globalVersion = 0;
  var Link = class {
    constructor(sub, dep) {
      this.sub = sub;
      this.dep = dep;
      this.version = dep.version;
      this.nextDep = this.prevDep = this.nextSub = this.prevSub = this.prevActiveLink = void 0;
    }
  };
  var Dep = class {
    // TODO isolatedDeclarations "__v_skip"
    constructor(computed3) {
      this.computed = computed3;
      this.version = 0;
      this.activeLink = void 0;
      this.subs = void 0;
      this.map = void 0;
      this.key = void 0;
      this.sc = 0;
      this.__v_skip = true;
      if (true) {
        this.subsHead = void 0;
      }
    }
    track(debugInfo) {
      if (!activeSub || !shouldTrack || activeSub === this.computed) {
        return;
      }
      let link = this.activeLink;
      if (link === void 0 || link.sub !== activeSub) {
        link = this.activeLink = new Link(activeSub, this);
        if (!activeSub.deps) {
          activeSub.deps = activeSub.depsTail = link;
        } else {
          link.prevDep = activeSub.depsTail;
          activeSub.depsTail.nextDep = link;
          activeSub.depsTail = link;
        }
        addSub(link);
      } else if (link.version === -1) {
        link.version = this.version;
        if (link.nextDep) {
          const next = link.nextDep;
          next.prevDep = link.prevDep;
          if (link.prevDep) {
            link.prevDep.nextDep = next;
          }
          link.prevDep = activeSub.depsTail;
          link.nextDep = void 0;
          activeSub.depsTail.nextDep = link;
          activeSub.depsTail = link;
          if (activeSub.deps === link) {
            activeSub.deps = next;
          }
        }
      }
      if (activeSub.onTrack) {
        activeSub.onTrack(
          extend(
            {
              effect: activeSub
            },
            debugInfo
          )
        );
      }
      return link;
    }
    trigger(debugInfo) {
      this.version++;
      globalVersion++;
      this.notify(debugInfo);
    }
    notify(debugInfo) {
      startBatch();
      try {
        if (true) {
          for (let head = this.subsHead; head; head = head.nextSub) {
            if (head.sub.onTrigger && !(head.sub.flags & 8)) {
              head.sub.onTrigger(
                extend(
                  {
                    effect: head.sub
                  },
                  debugInfo
                )
              );
            }
          }
        }
        for (let link = this.subs; link; link = link.prevSub) {
          if (link.sub.notify()) {
            ;
            link.sub.dep.notify();
          }
        }
      } finally {
        endBatch();
      }
    }
  };
  function addSub(link) {
    link.dep.sc++;
    if (link.sub.flags & 4) {
      const computed3 = link.dep.computed;
      if (computed3 && !link.dep.subs) {
        computed3.flags |= 4 | 16;
        for (let l = computed3.deps; l; l = l.nextDep) {
          addSub(l);
        }
      }
      const currentTail = link.dep.subs;
      if (currentTail !== link) {
        link.prevSub = currentTail;
        if (currentTail) currentTail.nextSub = link;
      }
      if (link.dep.subsHead === void 0) {
        link.dep.subsHead = link;
      }
      link.dep.subs = link;
    }
  }
  var targetMap = /* @__PURE__ */ new WeakMap();
  var ITERATE_KEY = /* @__PURE__ */ Symbol(
    true ? "Object iterate" : ""
  );
  var MAP_KEY_ITERATE_KEY = /* @__PURE__ */ Symbol(
    true ? "Map keys iterate" : ""
  );
  var ARRAY_ITERATE_KEY = /* @__PURE__ */ Symbol(
    true ? "Array iterate" : ""
  );
  function track(target, type, key) {
    if (shouldTrack && activeSub) {
      let depsMap = targetMap.get(target);
      if (!depsMap) {
        targetMap.set(target, depsMap = /* @__PURE__ */ new Map());
      }
      let dep = depsMap.get(key);
      if (!dep) {
        depsMap.set(key, dep = new Dep());
        dep.map = depsMap;
        dep.key = key;
      }
      if (true) {
        dep.track({
          target,
          type,
          key
        });
      } else {
        dep.track();
      }
    }
  }
  function trigger(target, type, key, newValue, oldValue, oldTarget) {
    const depsMap = targetMap.get(target);
    if (!depsMap) {
      globalVersion++;
      return;
    }
    const run = (dep) => {
      if (dep) {
        if (true) {
          dep.trigger({
            target,
            type,
            key,
            newValue,
            oldValue,
            oldTarget
          });
        } else {
          dep.trigger();
        }
      }
    };
    startBatch();
    if (type === "clear") {
      depsMap.forEach(run);
    } else {
      const targetIsArray = isArray(target);
      const isArrayIndex = targetIsArray && isIntegerKey(key);
      if (targetIsArray && key === "length") {
        const newLength = Number(newValue);
        depsMap.forEach((dep, key2) => {
          if (key2 === "length" || key2 === ARRAY_ITERATE_KEY || !isSymbol(key2) && key2 >= newLength) {
            run(dep);
          }
        });
      } else {
        if (key !== void 0 || depsMap.has(void 0)) {
          run(depsMap.get(key));
        }
        if (isArrayIndex) {
          run(depsMap.get(ARRAY_ITERATE_KEY));
        }
        switch (type) {
          case "add":
            if (!targetIsArray) {
              run(depsMap.get(ITERATE_KEY));
              if (isMap(target)) {
                run(depsMap.get(MAP_KEY_ITERATE_KEY));
              }
            } else if (isArrayIndex) {
              run(depsMap.get("length"));
            }
            break;
          case "delete":
            if (!targetIsArray) {
              run(depsMap.get(ITERATE_KEY));
              if (isMap(target)) {
                run(depsMap.get(MAP_KEY_ITERATE_KEY));
              }
            }
            break;
          case "set":
            if (isMap(target)) {
              run(depsMap.get(ITERATE_KEY));
            }
            break;
        }
      }
    }
    endBatch();
  }
  function getDepFromReactive(object2, key) {
    const depMap = targetMap.get(object2);
    return depMap && depMap.get(key);
  }
  function reactiveReadArray(array2) {
    const raw = /* @__PURE__ */ toRaw(array2);
    if (raw === array2) return raw;
    track(raw, "iterate", ARRAY_ITERATE_KEY);
    if (/* @__PURE__ */ isShallow(array2)) return raw;
    if (!/* @__PURE__ */ isReadonly(array2)) return raw.map(toReactive);
    return /* @__PURE__ */ isReactive(array2) ? raw.map((item) => toReadonly(toReactive(item))) : raw.map(toReadonly);
  }
  function shallowReadArray(arr) {
    track(arr = /* @__PURE__ */ toRaw(arr), "iterate", ARRAY_ITERATE_KEY);
    return arr;
  }
  function toWrapped(target, item) {
    if (/* @__PURE__ */ isReadonly(target)) {
      return /* @__PURE__ */ isReactive(target) ? toReadonly(toReactive(item)) : toReadonly(item);
    }
    return toReactive(item);
  }
  var arrayInstrumentations = {
    __proto__: null,
    [Symbol.iterator]() {
      return iterator(this, Symbol.iterator, (item) => toWrapped(this, item));
    },
    concat(...args) {
      return reactiveReadArray(this).concat(
        ...args.map((x) => isArray(x) ? reactiveReadArray(x) : x)
      );
    },
    entries() {
      return iterator(this, "entries", (value) => {
        value[1] = toWrapped(this, value[1]);
        return value;
      });
    },
    every(fn, thisArg) {
      return apply(this, "every", fn, thisArg, void 0, arguments);
    },
    filter(fn, thisArg) {
      return apply(
        this,
        "filter",
        fn,
        thisArg,
        (v) => v.map((item) => toWrapped(this, item)),
        arguments
      );
    },
    find(fn, thisArg) {
      return apply(
        this,
        "find",
        fn,
        thisArg,
        (item) => toWrapped(this, item),
        arguments
      );
    },
    findIndex(fn, thisArg) {
      return apply(this, "findIndex", fn, thisArg, void 0, arguments);
    },
    findLast(fn, thisArg) {
      return apply(
        this,
        "findLast",
        fn,
        thisArg,
        (item) => toWrapped(this, item),
        arguments
      );
    },
    findLastIndex(fn, thisArg) {
      return apply(this, "findLastIndex", fn, thisArg, void 0, arguments);
    },
    // flat, flatMap could benefit from ARRAY_ITERATE but are not straight-forward to implement
    forEach(fn, thisArg) {
      return apply(this, "forEach", fn, thisArg, void 0, arguments);
    },
    includes(...args) {
      return searchProxy(this, "includes", args);
    },
    indexOf(...args) {
      return searchProxy(this, "indexOf", args);
    },
    join(separator) {
      return reactiveReadArray(this).join(separator);
    },
    // keys() iterator only reads `length`, no optimization required
    lastIndexOf(...args) {
      return searchProxy(this, "lastIndexOf", args);
    },
    map(fn, thisArg) {
      return apply(this, "map", fn, thisArg, void 0, arguments);
    },
    pop() {
      return noTracking(this, "pop");
    },
    push(...args) {
      return noTracking(this, "push", args);
    },
    reduce(fn, ...args) {
      return reduce(this, "reduce", fn, args);
    },
    reduceRight(fn, ...args) {
      return reduce(this, "reduceRight", fn, args);
    },
    shift() {
      return noTracking(this, "shift");
    },
    // slice could use ARRAY_ITERATE but also seems to beg for range tracking
    some(fn, thisArg) {
      return apply(this, "some", fn, thisArg, void 0, arguments);
    },
    splice(...args) {
      return noTracking(this, "splice", args);
    },
    toReversed() {
      return reactiveReadArray(this).toReversed();
    },
    toSorted(comparer) {
      return reactiveReadArray(this).toSorted(comparer);
    },
    toSpliced(...args) {
      return reactiveReadArray(this).toSpliced(...args);
    },
    unshift(...args) {
      return noTracking(this, "unshift", args);
    },
    values() {
      return iterator(this, "values", (item) => toWrapped(this, item));
    }
  };
  function iterator(self2, method, wrapValue) {
    const arr = shallowReadArray(self2);
    const methodFn = arr[method];
    const iter = methodFn.call(arr);
    if (arr !== self2 && !/* @__PURE__ */ isShallow(self2)) {
      if (iter[Symbol.toStringTag] === "Array Iterator") {
        const wrapped = Object.create(Object.getPrototypeOf(iter));
        wrapped.next = () => wrapResult(iter.next(), wrapValue);
        if (methodFn !== arrayProto[method]) {
          Object.defineProperties(wrapped, {
            return: {
              get() {
                const fn = iter.return;
                return typeof fn === "function" ? fn.bind(iter) : fn;
              }
            },
            throw: {
              get() {
                const fn = iter.throw;
                return typeof fn === "function" ? fn.bind(iter) : fn;
              }
            }
          });
        }
        return wrapped;
      }
      iter._next = iter.next;
      iter.next = () => wrapResult(iter._next(), wrapValue);
    }
    return iter;
  }
  function wrapResult(result, wrapValue) {
    if (!result.done) {
      result.value = wrapValue(result.value);
    }
    return result;
  }
  var arrayProto = Array.prototype;
  function apply(self2, method, fn, thisArg, wrappedRetFn, args) {
    const arr = shallowReadArray(self2);
    const needsWrap = arr !== self2 && !/* @__PURE__ */ isShallow(self2);
    const methodFn = arr[method];
    if (methodFn !== arrayProto[method]) {
      const result2 = methodFn.apply(self2, args);
      return needsWrap ? toReactive(result2) : result2;
    }
    let wrappedFn = fn;
    if (arr !== self2) {
      if (needsWrap) {
        wrappedFn = function(item, index) {
          return fn.call(this, toWrapped(self2, item), index, self2);
        };
      } else if (fn.length > 2) {
        wrappedFn = function(item, index) {
          return fn.call(this, item, index, self2);
        };
      }
    }
    const result = methodFn.call(arr, wrappedFn, thisArg);
    return needsWrap && wrappedRetFn ? wrappedRetFn(result) : result;
  }
  function reduce(self2, method, fn, args) {
    const arr = shallowReadArray(self2);
    const needsWrap = arr !== self2 && !/* @__PURE__ */ isShallow(self2);
    let wrappedFn = fn;
    let wrapInitialAccumulator = false;
    if (arr !== self2) {
      if (needsWrap) {
        wrapInitialAccumulator = args.length === 0;
        wrappedFn = function(acc, item, index) {
          if (wrapInitialAccumulator) {
            wrapInitialAccumulator = false;
            acc = toWrapped(self2, acc);
          }
          return fn.call(this, acc, toWrapped(self2, item), index, self2);
        };
      } else if (fn.length > 3) {
        wrappedFn = function(acc, item, index) {
          return fn.call(this, acc, item, index, self2);
        };
      }
    }
    const result = arr[method](wrappedFn, ...args);
    return wrapInitialAccumulator ? toWrapped(self2, result) : result;
  }
  function searchProxy(self2, method, args) {
    const arr = /* @__PURE__ */ toRaw(self2);
    track(arr, "iterate", ARRAY_ITERATE_KEY);
    const res = arr[method](...args);
    if ((res === -1 || res === false) && /* @__PURE__ */ isProxy(args[0])) {
      args[0] = /* @__PURE__ */ toRaw(args[0]);
      return arr[method](...args);
    }
    return res;
  }
  function noTracking(self2, method, args = []) {
    pauseTracking();
    startBatch();
    const res = (/* @__PURE__ */ toRaw(self2))[method].apply(self2, args);
    endBatch();
    resetTracking();
    return res;
  }
  var isNonTrackableKeys = /* @__PURE__ */ makeMap(`__proto__,__v_isRef,__isVue`);
  var builtInSymbols = new Set(
    /* @__PURE__ */ Object.getOwnPropertyNames(Symbol).filter((key) => key !== "arguments" && key !== "caller").map((key) => Symbol[key]).filter(isSymbol)
  );
  function hasOwnProperty2(key) {
    if (!isSymbol(key)) key = String(key);
    const obj = /* @__PURE__ */ toRaw(this);
    track(obj, "has", key);
    return obj.hasOwnProperty(key);
  }
  var BaseReactiveHandler = class {
    constructor(_isReadonly = false, _isShallow = false) {
      this._isReadonly = _isReadonly;
      this._isShallow = _isShallow;
    }
    get(target, key, receiver) {
      if (key === "__v_skip") return target["__v_skip"];
      const isReadonly2 = this._isReadonly, isShallow2 = this._isShallow;
      if (key === "__v_isReactive") {
        return !isReadonly2;
      } else if (key === "__v_isReadonly") {
        return isReadonly2;
      } else if (key === "__v_isShallow") {
        return isShallow2;
      } else if (key === "__v_raw") {
        if (receiver === (isReadonly2 ? isShallow2 ? shallowReadonlyMap : readonlyMap : isShallow2 ? shallowReactiveMap : reactiveMap).get(target) || // receiver is not the reactive proxy, but has the same prototype
        // this means the receiver is a user proxy of the reactive proxy
        Object.getPrototypeOf(target) === Object.getPrototypeOf(receiver)) {
          return target;
        }
        return;
      }
      const targetIsArray = isArray(target);
      if (!isReadonly2) {
        let fn;
        if (targetIsArray && (fn = arrayInstrumentations[key])) {
          return fn;
        }
        if (key === "hasOwnProperty") {
          return hasOwnProperty2;
        }
      }
      const res = Reflect.get(
        target,
        key,
        // if this is a proxy wrapping a ref, return methods using the raw ref
        // as receiver so that we don't have to call `toRaw` on the ref in all
        // its class methods
        /* @__PURE__ */ isRef2(target) ? target : receiver
      );
      if (isSymbol(key) ? builtInSymbols.has(key) : isNonTrackableKeys(key)) {
        return res;
      }
      if (!isReadonly2) {
        track(target, "get", key);
      }
      if (isShallow2) {
        return res;
      }
      if (/* @__PURE__ */ isRef2(res)) {
        const value = targetIsArray && isIntegerKey(key) ? res : res.value;
        return isReadonly2 && isObject(value) ? /* @__PURE__ */ readonly(value) : value;
      }
      if (isObject(res)) {
        return isReadonly2 ? /* @__PURE__ */ readonly(res) : /* @__PURE__ */ reactive(res);
      }
      return res;
    }
  };
  var MutableReactiveHandler = class extends BaseReactiveHandler {
    constructor(isShallow2 = false) {
      super(false, isShallow2);
    }
    set(target, key, value, receiver) {
      let oldValue = target[key];
      const isArrayWithIntegerKey = isArray(target) && isIntegerKey(key);
      if (!this._isShallow) {
        const isOldValueReadonly = /* @__PURE__ */ isReadonly(oldValue);
        if (!/* @__PURE__ */ isShallow(value) && !/* @__PURE__ */ isReadonly(value)) {
          oldValue = /* @__PURE__ */ toRaw(oldValue);
          value = /* @__PURE__ */ toRaw(value);
        }
        if (!isArrayWithIntegerKey && /* @__PURE__ */ isRef2(oldValue) && !/* @__PURE__ */ isRef2(value)) {
          if (isOldValueReadonly) {
            if (true) {
              warn(
                `Set operation on key "${String(key)}" failed: target is readonly.`,
                target[key]
              );
            }
            return true;
          } else {
            oldValue.value = value;
            return true;
          }
        }
      }
      const hadKey = isArrayWithIntegerKey ? Number(key) < target.length : hasOwn(target, key);
      const result = Reflect.set(
        target,
        key,
        value,
        /* @__PURE__ */ isRef2(target) ? target : receiver
      );
      if (target === /* @__PURE__ */ toRaw(receiver) && result) {
        if (!hadKey) {
          trigger(target, "add", key, value);
        } else if (hasChanged(value, oldValue)) {
          trigger(target, "set", key, value, oldValue);
        }
      }
      return result;
    }
    deleteProperty(target, key) {
      const hadKey = hasOwn(target, key);
      const oldValue = target[key];
      const result = Reflect.deleteProperty(target, key);
      if (result && hadKey) {
        trigger(target, "delete", key, void 0, oldValue);
      }
      return result;
    }
    has(target, key) {
      const result = Reflect.has(target, key);
      if (!isSymbol(key) || !builtInSymbols.has(key)) {
        track(target, "has", key);
      }
      return result;
    }
    ownKeys(target) {
      track(
        target,
        "iterate",
        isArray(target) ? "length" : ITERATE_KEY
      );
      return Reflect.ownKeys(target);
    }
  };
  var ReadonlyReactiveHandler = class extends BaseReactiveHandler {
    constructor(isShallow2 = false) {
      super(true, isShallow2);
    }
    set(target, key) {
      if (true) {
        warn(
          `Set operation on key "${String(key)}" failed: target is readonly.`,
          target
        );
      }
      return true;
    }
    deleteProperty(target, key) {
      if (true) {
        warn(
          `Delete operation on key "${String(key)}" failed: target is readonly.`,
          target
        );
      }
      return true;
    }
  };
  var mutableHandlers = /* @__PURE__ */ new MutableReactiveHandler();
  var readonlyHandlers = /* @__PURE__ */ new ReadonlyReactiveHandler();
  var shallowReactiveHandlers = /* @__PURE__ */ new MutableReactiveHandler(true);
  var shallowReadonlyHandlers = /* @__PURE__ */ new ReadonlyReactiveHandler(true);
  var toShallow = (value) => value;
  var getProto = (v) => Reflect.getPrototypeOf(v);
  function createIterableMethod(method, isReadonly2, isShallow2) {
    return function(...args) {
      const target = this["__v_raw"];
      const rawTarget = /* @__PURE__ */ toRaw(target);
      const targetIsMap = isMap(rawTarget);
      const isPair = method === "entries" || method === Symbol.iterator && targetIsMap;
      const isKeyOnly = method === "keys" && targetIsMap;
      const innerIterator = target[method](...args);
      const wrap = isShallow2 ? toShallow : isReadonly2 ? toReadonly : toReactive;
      !isReadonly2 && track(
        rawTarget,
        "iterate",
        isKeyOnly ? MAP_KEY_ITERATE_KEY : ITERATE_KEY
      );
      return extend(
        // inheriting all iterator properties
        Object.create(innerIterator),
        {
          // iterator protocol
          next() {
            const { value, done } = innerIterator.next();
            return done ? { value, done } : {
              value: isPair ? [wrap(value[0]), wrap(value[1])] : wrap(value),
              done
            };
          }
        }
      );
    };
  }
  function createReadonlyMethod(type) {
    return function(...args) {
      if (true) {
        const key = args[0] ? `on key "${args[0]}" ` : ``;
        warn(
          `${capitalize(type)} operation ${key}failed: target is readonly.`,
          /* @__PURE__ */ toRaw(this)
        );
      }
      return type === "delete" ? false : type === "clear" ? void 0 : this;
    };
  }
  function createInstrumentations(readonly2, shallow) {
    const instrumentations = {
      get(key) {
        const target = this["__v_raw"];
        const rawTarget = /* @__PURE__ */ toRaw(target);
        const rawKey = /* @__PURE__ */ toRaw(key);
        if (!readonly2) {
          if (hasChanged(key, rawKey)) {
            track(rawTarget, "get", key);
          }
          track(rawTarget, "get", rawKey);
        }
        const { has } = getProto(rawTarget);
        const wrap = shallow ? toShallow : readonly2 ? toReadonly : toReactive;
        if (has.call(rawTarget, key)) {
          return wrap(target.get(key));
        } else if (has.call(rawTarget, rawKey)) {
          return wrap(target.get(rawKey));
        } else if (target !== rawTarget) {
          target.get(key);
        }
      },
      get size() {
        const target = this["__v_raw"];
        !readonly2 && track(/* @__PURE__ */ toRaw(target), "iterate", ITERATE_KEY);
        return target.size;
      },
      has(key) {
        const target = this["__v_raw"];
        const rawTarget = /* @__PURE__ */ toRaw(target);
        const rawKey = /* @__PURE__ */ toRaw(key);
        if (!readonly2) {
          if (hasChanged(key, rawKey)) {
            track(rawTarget, "has", key);
          }
          track(rawTarget, "has", rawKey);
        }
        return key === rawKey ? target.has(key) : target.has(key) || target.has(rawKey);
      },
      forEach(callback, thisArg) {
        const observed = this;
        const target = observed["__v_raw"];
        const rawTarget = /* @__PURE__ */ toRaw(target);
        const wrap = shallow ? toShallow : readonly2 ? toReadonly : toReactive;
        !readonly2 && track(rawTarget, "iterate", ITERATE_KEY);
        return target.forEach((value, key) => {
          return callback.call(thisArg, wrap(value), wrap(key), observed);
        });
      }
    };
    extend(
      instrumentations,
      readonly2 ? {
        add: createReadonlyMethod("add"),
        set: createReadonlyMethod("set"),
        delete: createReadonlyMethod("delete"),
        clear: createReadonlyMethod("clear")
      } : {
        add(value) {
          const target = /* @__PURE__ */ toRaw(this);
          const proto = getProto(target);
          const rawValue = /* @__PURE__ */ toRaw(value);
          const valueToAdd = !shallow && !/* @__PURE__ */ isShallow(value) && !/* @__PURE__ */ isReadonly(value) ? rawValue : value;
          const hadKey = proto.has.call(target, valueToAdd) || hasChanged(value, valueToAdd) && proto.has.call(target, value) || hasChanged(rawValue, valueToAdd) && proto.has.call(target, rawValue);
          if (!hadKey) {
            target.add(valueToAdd);
            trigger(target, "add", valueToAdd, valueToAdd);
          }
          return this;
        },
        set(key, value) {
          if (!shallow && !/* @__PURE__ */ isShallow(value) && !/* @__PURE__ */ isReadonly(value)) {
            value = /* @__PURE__ */ toRaw(value);
          }
          const target = /* @__PURE__ */ toRaw(this);
          const { has, get: get2 } = getProto(target);
          let hadKey = has.call(target, key);
          if (!hadKey) {
            key = /* @__PURE__ */ toRaw(key);
            hadKey = has.call(target, key);
          } else if (true) {
            checkIdentityKeys(target, has, key);
          }
          const oldValue = get2.call(target, key);
          target.set(key, value);
          if (!hadKey) {
            trigger(target, "add", key, value);
          } else if (hasChanged(value, oldValue)) {
            trigger(target, "set", key, value, oldValue);
          }
          return this;
        },
        delete(key) {
          const target = /* @__PURE__ */ toRaw(this);
          const { has, get: get2 } = getProto(target);
          let hadKey = has.call(target, key);
          if (!hadKey) {
            key = /* @__PURE__ */ toRaw(key);
            hadKey = has.call(target, key);
          } else if (true) {
            checkIdentityKeys(target, has, key);
          }
          const oldValue = get2 ? get2.call(target, key) : void 0;
          const result = target.delete(key);
          if (hadKey) {
            trigger(target, "delete", key, void 0, oldValue);
          }
          return result;
        },
        clear() {
          const target = /* @__PURE__ */ toRaw(this);
          const hadItems = target.size !== 0;
          const oldTarget = true ? isMap(target) ? new Map(target) : new Set(target) : void 0;
          const result = target.clear();
          if (hadItems) {
            trigger(
              target,
              "clear",
              void 0,
              void 0,
              oldTarget
            );
          }
          return result;
        }
      }
    );
    const iteratorMethods = [
      "keys",
      "values",
      "entries",
      Symbol.iterator
    ];
    iteratorMethods.forEach((method) => {
      instrumentations[method] = createIterableMethod(method, readonly2, shallow);
    });
    return instrumentations;
  }
  function createInstrumentationGetter(isReadonly2, shallow) {
    const instrumentations = createInstrumentations(isReadonly2, shallow);
    return (target, key, receiver) => {
      if (key === "__v_isReactive") {
        return !isReadonly2;
      } else if (key === "__v_isReadonly") {
        return isReadonly2;
      } else if (key === "__v_isShallow") {
        return shallow;
      } else if (key === "__v_raw") {
        return target;
      }
      return Reflect.get(
        hasOwn(instrumentations, key) && key in target ? instrumentations : target,
        key,
        receiver
      );
    };
  }
  var mutableCollectionHandlers = {
    get: /* @__PURE__ */ createInstrumentationGetter(false, false)
  };
  var shallowCollectionHandlers = {
    get: /* @__PURE__ */ createInstrumentationGetter(false, true)
  };
  var readonlyCollectionHandlers = {
    get: /* @__PURE__ */ createInstrumentationGetter(true, false)
  };
  var shallowReadonlyCollectionHandlers = {
    get: /* @__PURE__ */ createInstrumentationGetter(true, true)
  };
  function checkIdentityKeys(target, has, key) {
    const rawKey = /* @__PURE__ */ toRaw(key);
    if (rawKey !== key && has.call(target, rawKey)) {
      const type = toRawType(target);
      warn(
        `Reactive ${type} contains both the raw and reactive versions of the same object${type === `Map` ? ` as keys` : ``}, which can lead to inconsistencies. Avoid differentiating between the raw and reactive versions of an object and only use the reactive version if possible.`
      );
    }
  }
  var reactiveMap = /* @__PURE__ */ new WeakMap();
  var shallowReactiveMap = /* @__PURE__ */ new WeakMap();
  var readonlyMap = /* @__PURE__ */ new WeakMap();
  var shallowReadonlyMap = /* @__PURE__ */ new WeakMap();
  function targetTypeMap(rawType) {
    switch (rawType) {
      case "Object":
      case "Array":
        return 1;
      case "Map":
      case "Set":
      case "WeakMap":
      case "WeakSet":
        return 2;
      default:
        return 0;
    }
  }
  // @__NO_SIDE_EFFECTS__
  function reactive(target) {
    if (/* @__PURE__ */ isReadonly(target)) {
      return target;
    }
    return createReactiveObject(
      target,
      false,
      mutableHandlers,
      mutableCollectionHandlers,
      reactiveMap
    );
  }
  // @__NO_SIDE_EFFECTS__
  function shallowReactive(target) {
    return createReactiveObject(
      target,
      false,
      shallowReactiveHandlers,
      shallowCollectionHandlers,
      shallowReactiveMap
    );
  }
  // @__NO_SIDE_EFFECTS__
  function readonly(target) {
    return createReactiveObject(
      target,
      true,
      readonlyHandlers,
      readonlyCollectionHandlers,
      readonlyMap
    );
  }
  // @__NO_SIDE_EFFECTS__
  function shallowReadonly(target) {
    return createReactiveObject(
      target,
      true,
      shallowReadonlyHandlers,
      shallowReadonlyCollectionHandlers,
      shallowReadonlyMap
    );
  }
  function createReactiveObject(target, isReadonly2, baseHandlers, collectionHandlers, proxyMap) {
    if (!isObject(target)) {
      if (true) {
        warn(
          `value cannot be made ${isReadonly2 ? "readonly" : "reactive"}: ${String(
            target
          )}`
        );
      }
      return target;
    }
    if (target["__v_raw"] && !(isReadonly2 && target["__v_isReactive"])) {
      return target;
    }
    if (target["__v_skip"] || !Object.isExtensible(target)) {
      return target;
    }
    const existingProxy = proxyMap.get(target);
    if (existingProxy) {
      return existingProxy;
    }
    const targetType = targetTypeMap(toRawType(target));
    if (targetType === 0) {
      return target;
    }
    const proxy = new Proxy(
      target,
      targetType === 2 ? collectionHandlers : baseHandlers
    );
    proxyMap.set(target, proxy);
    return proxy;
  }
  // @__NO_SIDE_EFFECTS__
  function isReactive(value) {
    if (/* @__PURE__ */ isReadonly(value)) {
      return /* @__PURE__ */ isReactive(value["__v_raw"]);
    }
    return !!(value && value["__v_isReactive"]);
  }
  // @__NO_SIDE_EFFECTS__
  function isReadonly(value) {
    return !!(value && value["__v_isReadonly"]);
  }
  // @__NO_SIDE_EFFECTS__
  function isShallow(value) {
    return !!(value && value["__v_isShallow"]);
  }
  // @__NO_SIDE_EFFECTS__
  function isProxy(value) {
    return value ? !!value["__v_raw"] : false;
  }
  // @__NO_SIDE_EFFECTS__
  function toRaw(observed) {
    const raw = observed && observed["__v_raw"];
    return raw ? /* @__PURE__ */ toRaw(raw) : observed;
  }
  function markRaw(value) {
    if (!hasOwn(value, "__v_skip") && Object.isExtensible(value)) {
      def(value, "__v_skip", true);
    }
    return value;
  }
  var toReactive = (value) => isObject(value) ? /* @__PURE__ */ reactive(value) : value;
  var toReadonly = (value) => isObject(value) ? /* @__PURE__ */ readonly(value) : value;
  // @__NO_SIDE_EFFECTS__
  function isRef2(r) {
    return r ? r["__v_isRef"] === true : false;
  }
  // @__NO_SIDE_EFFECTS__
  function ref(value) {
    return createRef(value, false);
  }
  // @__NO_SIDE_EFFECTS__
  function shallowRef(value) {
    return createRef(value, true);
  }
  function createRef(rawValue, shallow) {
    if (/* @__PURE__ */ isRef2(rawValue)) {
      return rawValue;
    }
    return new RefImpl(rawValue, shallow);
  }
  var RefImpl = class {
    constructor(value, isShallow2) {
      this.dep = new Dep();
      this["__v_isRef"] = true;
      this["__v_isShallow"] = false;
      this._rawValue = isShallow2 ? value : /* @__PURE__ */ toRaw(value);
      this._value = isShallow2 ? value : toReactive(value);
      this["__v_isShallow"] = isShallow2;
    }
    get value() {
      if (true) {
        this.dep.track({
          target: this,
          type: "get",
          key: "value"
        });
      } else {
        this.dep.track();
      }
      return this._value;
    }
    set value(newValue) {
      const oldValue = this._rawValue;
      const useDirectValue = this["__v_isShallow"] || /* @__PURE__ */ isShallow(newValue) || /* @__PURE__ */ isReadonly(newValue);
      newValue = useDirectValue ? newValue : /* @__PURE__ */ toRaw(newValue);
      if (hasChanged(newValue, oldValue)) {
        this._rawValue = newValue;
        this._value = useDirectValue ? newValue : toReactive(newValue);
        if (true) {
          this.dep.trigger({
            target: this,
            type: "set",
            key: "value",
            newValue,
            oldValue
          });
        } else {
          this.dep.trigger();
        }
      }
    }
  };
  function unref(ref2) {
    return /* @__PURE__ */ isRef2(ref2) ? ref2.value : ref2;
  }
  function toValue(source) {
    return isFunction(source) ? source() : unref(source);
  }
  var shallowUnwrapHandlers = {
    get: (target, key, receiver) => key === "__v_raw" ? target : unref(Reflect.get(target, key, receiver)),
    set: (target, key, value, receiver) => {
      const oldValue = target[key];
      if (/* @__PURE__ */ isRef2(oldValue) && !/* @__PURE__ */ isRef2(value)) {
        oldValue.value = value;
        return true;
      } else {
        return Reflect.set(target, key, value, receiver);
      }
    }
  };
  function proxyRefs(objectWithRefs) {
    return /* @__PURE__ */ isReactive(objectWithRefs) ? objectWithRefs : new Proxy(objectWithRefs, shallowUnwrapHandlers);
  }
  var CustomRefImpl = class {
    constructor(factory) {
      this["__v_isRef"] = true;
      this._value = void 0;
      const dep = this.dep = new Dep();
      const { get: get2, set: set2 } = factory(dep.track.bind(dep), dep.trigger.bind(dep));
      this._get = get2;
      this._set = set2;
    }
    get value() {
      return this._value = this._get();
    }
    set value(newVal) {
      this._set(newVal);
    }
  };
  function customRef(factory) {
    return new CustomRefImpl(factory);
  }
  // @__NO_SIDE_EFFECTS__
  function toRefs(object2) {
    if (!/* @__PURE__ */ isProxy(object2)) {
      warn(`toRefs() expects a reactive object but received a plain one.`);
    }
    const ret = isArray(object2) ? new Array(object2.length) : {};
    for (const key in object2) {
      ret[key] = propertyToRef(object2, key);
    }
    return ret;
  }
  var ObjectRefImpl = class {
    constructor(_object, key, _defaultValue) {
      this._object = _object;
      this._defaultValue = _defaultValue;
      this["__v_isRef"] = true;
      this._value = void 0;
      this._key = isSymbol(key) ? key : String(key);
      this._raw = /* @__PURE__ */ toRaw(_object);
      let shallow = true;
      let obj = _object;
      if (!isArray(_object) || isSymbol(this._key) || !isIntegerKey(this._key)) {
        do {
          shallow = !/* @__PURE__ */ isProxy(obj) || /* @__PURE__ */ isShallow(obj);
        } while (shallow && (obj = obj["__v_raw"]));
      }
      this._shallow = shallow;
    }
    get value() {
      let val = this._object[this._key];
      if (this._shallow) {
        val = unref(val);
      }
      return this._value = val === void 0 ? this._defaultValue : val;
    }
    set value(newVal) {
      if (this._shallow && /* @__PURE__ */ isRef2(this._raw[this._key])) {
        const nestedRef = this._object[this._key];
        if (/* @__PURE__ */ isRef2(nestedRef)) {
          nestedRef.value = newVal;
          return;
        }
      }
      this._object[this._key] = newVal;
    }
    get dep() {
      return getDepFromReactive(this._raw, this._key);
    }
  };
  var GetterRefImpl = class {
    constructor(_getter) {
      this._getter = _getter;
      this["__v_isRef"] = true;
      this["__v_isReadonly"] = true;
      this._value = void 0;
    }
    get value() {
      return this._value = this._getter();
    }
  };
  // @__NO_SIDE_EFFECTS__
  function toRef(source, key, defaultValue) {
    if (/* @__PURE__ */ isRef2(source)) {
      return source;
    } else if (isFunction(source)) {
      return new GetterRefImpl(source);
    } else if (isObject(source) && arguments.length > 1) {
      return propertyToRef(source, key, defaultValue);
    } else {
      return /* @__PURE__ */ ref(source);
    }
  }
  function propertyToRef(source, key, defaultValue) {
    return new ObjectRefImpl(source, key, defaultValue);
  }
  var ComputedRefImpl = class {
    constructor(fn, setter, isSSR) {
      this.fn = fn;
      this.setter = setter;
      this._value = void 0;
      this.dep = new Dep(this);
      this.__v_isRef = true;
      this.deps = void 0;
      this.depsTail = void 0;
      this.flags = 16;
      this.globalVersion = globalVersion - 1;
      this.next = void 0;
      this.effect = this;
      this["__v_isReadonly"] = !setter;
      this.isSSR = isSSR;
    }
    /**
     * @internal
     */
    notify() {
      this.flags |= 16;
      if (!(this.flags & 8) && // avoid infinite self recursion
      activeSub !== this) {
        batch(this, true);
        return true;
      } else if (true) ;
    }
    get value() {
      const link = true ? this.dep.track({
        target: this,
        type: "get",
        key: "value"
      }) : this.dep.track();
      refreshComputed(this);
      if (link) {
        link.version = this.dep.version;
      }
      return this._value;
    }
    set value(newValue) {
      if (this.setter) {
        this.setter(newValue);
      } else if (true) {
        warn("Write operation failed: computed value is readonly");
      }
    }
  };
  // @__NO_SIDE_EFFECTS__
  function computed(getterOrOptions, debugOptions, isSSR = false) {
    let getter;
    let setter;
    if (isFunction(getterOrOptions)) {
      getter = getterOrOptions;
    } else {
      getter = getterOrOptions.get;
      setter = getterOrOptions.set;
    }
    const cRef = new ComputedRefImpl(getter, setter, isSSR);
    if (debugOptions && !isSSR) {
      cRef.onTrack = debugOptions.onTrack;
      cRef.onTrigger = debugOptions.onTrigger;
    }
    return cRef;
  }
  var INITIAL_WATCHER_VALUE = {};
  var cleanupMap = /* @__PURE__ */ new WeakMap();
  var activeWatcher = void 0;
  function onWatcherCleanup(cleanupFn, failSilently = false, owner = activeWatcher) {
    if (owner) {
      let cleanups = cleanupMap.get(owner);
      if (!cleanups) cleanupMap.set(owner, cleanups = []);
      cleanups.push(cleanupFn);
    } else if (!failSilently) {
      warn(
        `onWatcherCleanup() was called when there was no active watcher to associate with.`
      );
    }
  }
  function watch(source, cb, options = EMPTY_OBJ) {
    const { immediate, deep, once, scheduler, augmentJob, call } = options;
    const warnInvalidSource = (s) => {
      (options.onWarn || warn)(
        `Invalid watch source: `,
        s,
        `A watch source can only be a getter/effect function, a ref, a reactive object, or an array of these types.`
      );
    };
    const reactiveGetter = (source2) => {
      if (deep) return source2;
      if (/* @__PURE__ */ isShallow(source2) || deep === false || deep === 0)
        return traverse(source2, 1);
      return traverse(source2);
    };
    let effect2;
    let getter;
    let cleanup;
    let boundCleanup;
    let forceTrigger = false;
    let isMultiSource = false;
    if (/* @__PURE__ */ isRef2(source)) {
      getter = () => source.value;
      forceTrigger = /* @__PURE__ */ isShallow(source);
    } else if (/* @__PURE__ */ isReactive(source)) {
      getter = () => reactiveGetter(source);
      forceTrigger = true;
    } else if (isArray(source)) {
      isMultiSource = true;
      forceTrigger = source.some((s) => /* @__PURE__ */ isReactive(s) || /* @__PURE__ */ isShallow(s));
      getter = () => source.map((s) => {
        if (/* @__PURE__ */ isRef2(s)) {
          return s.value;
        } else if (/* @__PURE__ */ isReactive(s)) {
          return reactiveGetter(s);
        } else if (isFunction(s)) {
          return call ? call(s, 2) : s();
        } else {
          warnInvalidSource(s);
        }
      });
    } else if (isFunction(source)) {
      if (cb) {
        getter = call ? () => call(source, 2) : source;
      } else {
        getter = () => {
          if (cleanup) {
            pauseTracking();
            try {
              cleanup();
            } finally {
              resetTracking();
            }
          }
          const currentEffect = activeWatcher;
          activeWatcher = effect2;
          try {
            return call ? call(source, 3, [boundCleanup]) : source(boundCleanup);
          } finally {
            activeWatcher = currentEffect;
          }
        };
      }
    } else {
      getter = NOOP;
      warnInvalidSource(source);
    }
    if (cb && deep) {
      const baseGetter = getter;
      const depth = deep === true ? Infinity : deep;
      getter = () => traverse(baseGetter(), depth);
    }
    const scope = getCurrentScope();
    const watchHandle = () => {
      effect2.stop();
      if (scope && scope.active) {
        remove(scope.effects, effect2);
      }
    };
    if (once && cb) {
      const _cb = cb;
      cb = (...args) => {
        try {
          return _cb(...args);
        } finally {
          watchHandle();
        }
      };
    }
    let oldValue = isMultiSource ? new Array(source.length).fill(INITIAL_WATCHER_VALUE) : INITIAL_WATCHER_VALUE;
    const job = (immediateFirstRun) => {
      if (!(effect2.flags & 1) || !effect2.dirty && !immediateFirstRun) {
        return;
      }
      if (cb) {
        const newValue = effect2.run();
        if (immediateFirstRun || deep || forceTrigger || (isMultiSource ? newValue.some((v, i) => hasChanged(v, oldValue[i])) : hasChanged(newValue, oldValue))) {
          const currentWatcher = activeWatcher;
          pauseTracking();
          try {
            if (cleanup) {
              cleanup();
            }
            activeWatcher = effect2;
            const args = [
              newValue,
              // pass undefined as the old value when it's changed for the first time
              oldValue === INITIAL_WATCHER_VALUE ? void 0 : isMultiSource && oldValue[0] === INITIAL_WATCHER_VALUE ? [] : oldValue,
              boundCleanup
            ];
            oldValue = newValue;
            call ? call(cb, 3, args) : (
              // @ts-expect-error
              cb(...args)
            );
          } finally {
            activeWatcher = currentWatcher;
            resetTracking();
          }
        }
      } else {
        effect2.run();
      }
    };
    if (augmentJob) {
      augmentJob(job);
    }
    effect2 = new ReactiveEffect(getter);
    effect2.scheduler = scheduler ? () => scheduler(job, false) : job;
    boundCleanup = (fn) => onWatcherCleanup(fn, false, effect2);
    cleanup = effect2.onStop = () => {
      const cleanups = cleanupMap.get(effect2);
      if (cleanups) {
        if (call) {
          call(cleanups, 4);
        } else {
          for (const cleanup2 of cleanups) cleanup2();
        }
        cleanupMap.delete(effect2);
      }
    };
    if (true) {
      effect2.onTrack = options.onTrack;
      effect2.onTrigger = options.onTrigger;
    }
    if (cb) {
      if (immediate) {
        job(true);
      } else {
        oldValue = effect2.run();
      }
    } else if (scheduler) {
      scheduler(job.bind(null, true), true);
    } else {
      effect2.run();
    }
    watchHandle.pause = effect2.pause.bind(effect2);
    watchHandle.resume = effect2.resume.bind(effect2);
    watchHandle.stop = watchHandle;
    return watchHandle;
  }
  function traverse(value, depth = Infinity, seen) {
    if (depth <= 0 || !isObject(value) || value["__v_skip"]) {
      return value;
    }
    seen = seen || /* @__PURE__ */ new Map();
    if ((seen.get(value) || 0) >= depth) {
      return value;
    }
    seen.set(value, depth);
    depth--;
    if (/* @__PURE__ */ isRef2(value)) {
      traverse(value.value, depth, seen);
    } else if (isArray(value)) {
      for (let i = 0; i < value.length; i++) {
        traverse(value[i], depth, seen);
      }
    } else if (isSet(value) || isMap(value)) {
      value.forEach((v) => {
        traverse(v, depth, seen);
      });
    } else if (isPlainObject(value)) {
      for (const key in value) {
        traverse(value[key], depth, seen);
      }
      for (const key of Object.getOwnPropertySymbols(value)) {
        if (Object.prototype.propertyIsEnumerable.call(value, key)) {
          traverse(value[key], depth, seen);
        }
      }
    }
    return value;
  }

  // node_modules/@vue/runtime-core/dist/runtime-core.esm-bundler.js
  var stack = [];
  function pushWarningContext(vnode) {
    stack.push(vnode);
  }
  function popWarningContext() {
    stack.pop();
  }
  var isWarning = false;
  function warn$1(msg, ...args) {
    if (isWarning) return;
    isWarning = true;
    pauseTracking();
    const instance = stack.length ? stack[stack.length - 1].component : null;
    const appWarnHandler = instance && instance.appContext.config.warnHandler;
    const trace = getComponentTrace();
    if (appWarnHandler) {
      callWithErrorHandling(
        appWarnHandler,
        instance,
        11,
        [
          // eslint-disable-next-line no-restricted-syntax
          msg + args.map((a) => {
            var _a, _b;
            return (_b = (_a = a.toString) == null ? void 0 : _a.call(a)) != null ? _b : JSON.stringify(a);
          }).join(""),
          instance && instance.proxy,
          trace.map(
            ({ vnode }) => `at <${formatComponentName(instance, vnode.type)}>`
          ).join("\n"),
          trace
        ]
      );
    } else {
      const warnArgs = [`[Vue warn]: ${msg}`, ...args];
      if (trace.length && // avoid spamming console during tests
      true) {
        warnArgs.push(`
`, ...formatTrace(trace));
      }
      console.warn(...warnArgs);
    }
    resetTracking();
    isWarning = false;
  }
  function getComponentTrace() {
    let currentVNode = stack[stack.length - 1];
    if (!currentVNode) {
      return [];
    }
    const normalizedStack = [];
    while (currentVNode) {
      const last = normalizedStack[0];
      if (last && last.vnode === currentVNode) {
        last.recurseCount++;
      } else {
        normalizedStack.push({
          vnode: currentVNode,
          recurseCount: 0
        });
      }
      const parentInstance = currentVNode.component && currentVNode.component.parent;
      currentVNode = parentInstance && parentInstance.vnode;
    }
    return normalizedStack;
  }
  function formatTrace(trace) {
    const logs = [];
    trace.forEach((entry, i) => {
      logs.push(...i === 0 ? [] : [`
`], ...formatTraceEntry(entry));
    });
    return logs;
  }
  function formatTraceEntry({ vnode, recurseCount }) {
    const postfix = recurseCount > 0 ? `... (${recurseCount} recursive calls)` : ``;
    const isRoot = vnode.component ? vnode.component.parent == null : false;
    const open = ` at <${formatComponentName(
      vnode.component,
      vnode.type,
      isRoot
    )}`;
    const close = `>` + postfix;
    return vnode.props ? [open, ...formatProps(vnode.props), close] : [open + close];
  }
  function formatProps(props) {
    const res = [];
    const keys = Object.keys(props);
    keys.slice(0, 3).forEach((key) => {
      res.push(...formatProp(key, props[key]));
    });
    if (keys.length > 3) {
      res.push(` ...`);
    }
    return res;
  }
  function formatProp(key, value, raw) {
    if (isString(value)) {
      value = JSON.stringify(value);
      return raw ? value : [`${key}=${value}`];
    } else if (typeof value === "number" || typeof value === "boolean" || value == null) {
      return raw ? value : [`${key}=${value}`];
    } else if (isRef2(value)) {
      value = formatProp(key, toRaw(value.value), true);
      return raw ? value : [`${key}=Ref<`, value, `>`];
    } else if (isFunction(value)) {
      return [`${key}=fn${value.name ? `<${value.name}>` : ``}`];
    } else {
      value = toRaw(value);
      return raw ? value : [`${key}=`, value];
    }
  }
  var ErrorTypeStrings$1 = {
    ["sp"]: "serverPrefetch hook",
    ["bc"]: "beforeCreate hook",
    ["c"]: "created hook",
    ["bm"]: "beforeMount hook",
    ["m"]: "mounted hook",
    ["bu"]: "beforeUpdate hook",
    ["u"]: "updated",
    ["bum"]: "beforeUnmount hook",
    ["um"]: "unmounted hook",
    ["a"]: "activated hook",
    ["da"]: "deactivated hook",
    ["ec"]: "errorCaptured hook",
    ["rtc"]: "renderTracked hook",
    ["rtg"]: "renderTriggered hook",
    [0]: "setup function",
    [1]: "render function",
    [2]: "watcher getter",
    [3]: "watcher callback",
    [4]: "watcher cleanup function",
    [5]: "native event handler",
    [6]: "component event handler",
    [7]: "vnode hook",
    [8]: "directive hook",
    [9]: "transition hook",
    [10]: "app errorHandler",
    [11]: "app warnHandler",
    [12]: "ref function",
    [13]: "async component loader",
    [14]: "scheduler flush",
    [15]: "component update",
    [16]: "app unmount cleanup function"
  };
  function callWithErrorHandling(fn, instance, type, args) {
    try {
      return args ? fn(...args) : fn();
    } catch (err) {
      handleError(err, instance, type);
    }
  }
  function callWithAsyncErrorHandling(fn, instance, type, args) {
    if (isFunction(fn)) {
      const res = callWithErrorHandling(fn, instance, type, args);
      if (res && isPromise(res)) {
        res.catch((err) => {
          handleError(err, instance, type);
        });
      }
      return res;
    }
    if (isArray(fn)) {
      const values = [];
      for (let i = 0; i < fn.length; i++) {
        values.push(callWithAsyncErrorHandling(fn[i], instance, type, args));
      }
      return values;
    } else if (true) {
      warn$1(
        `Invalid value type passed to callWithAsyncErrorHandling(): ${typeof fn}`
      );
    }
  }
  function handleError(err, instance, type, throwInDev = true) {
    const contextVNode = instance ? instance.vnode : null;
    const { errorHandler, throwUnhandledErrorInProduction } = instance && instance.appContext.config || EMPTY_OBJ;
    if (instance) {
      let cur = instance.parent;
      const exposedInstance = instance.proxy;
      const errorInfo = true ? ErrorTypeStrings$1[type] : `https://vuejs.org/error-reference/#runtime-${type}`;
      while (cur) {
        const errorCapturedHooks = cur.ec;
        if (errorCapturedHooks) {
          for (let i = 0; i < errorCapturedHooks.length; i++) {
            if (errorCapturedHooks[i](err, exposedInstance, errorInfo) === false) {
              return;
            }
          }
        }
        cur = cur.parent;
      }
      if (errorHandler) {
        pauseTracking();
        callWithErrorHandling(errorHandler, null, 10, [
          err,
          exposedInstance,
          errorInfo
        ]);
        resetTracking();
        return;
      }
    }
    logError(err, type, contextVNode, throwInDev, throwUnhandledErrorInProduction);
  }
  function logError(err, type, contextVNode, throwInDev = true, throwInProd = false) {
    if (true) {
      const info = ErrorTypeStrings$1[type];
      if (contextVNode) {
        pushWarningContext(contextVNode);
      }
      warn$1(`Unhandled error${info ? ` during execution of ${info}` : ``}`);
      if (contextVNode) {
        popWarningContext();
      }
      if (throwInDev) {
        throw err;
      } else {
        console.error(err);
      }
    } else if (throwInProd) {
      throw err;
    } else {
      console.error(err);
    }
  }
  var queue = [];
  var flushIndex = -1;
  var pendingPostFlushCbs = [];
  var activePostFlushCbs = null;
  var postFlushIndex = 0;
  var resolvedPromise = /* @__PURE__ */ Promise.resolve();
  var currentFlushPromise = null;
  var RECURSION_LIMIT = 100;
  function nextTick(fn) {
    const p2 = currentFlushPromise || resolvedPromise;
    return fn ? p2.then(this ? fn.bind(this) : fn) : p2;
  }
  function findInsertionIndex(id2) {
    let start2 = flushIndex + 1;
    let end = queue.length;
    while (start2 < end) {
      const middle = start2 + end >>> 1;
      const middleJob = queue[middle];
      const middleJobId = getId(middleJob);
      if (middleJobId < id2 || middleJobId === id2 && middleJob.flags & 2) {
        start2 = middle + 1;
      } else {
        end = middle;
      }
    }
    return start2;
  }
  function queueJob(job) {
    if (!(job.flags & 1)) {
      const jobId = getId(job);
      const lastJob = queue[queue.length - 1];
      if (!lastJob || // fast path when the job id is larger than the tail
      !(job.flags & 2) && jobId >= getId(lastJob)) {
        queue.push(job);
      } else {
        queue.splice(findInsertionIndex(jobId), 0, job);
      }
      job.flags |= 1;
      queueFlush();
    }
  }
  function queueFlush() {
    if (!currentFlushPromise) {
      currentFlushPromise = resolvedPromise.then(flushJobs);
    }
  }
  function queuePostFlushCb(cb) {
    if (!isArray(cb)) {
      if (activePostFlushCbs && cb.id === -1) {
        activePostFlushCbs.splice(postFlushIndex + 1, 0, cb);
      } else if (!(cb.flags & 1)) {
        pendingPostFlushCbs.push(cb);
        cb.flags |= 1;
      }
    } else {
      for (let i = 0; i < cb.length; i++) {
        pendingPostFlushCbs.push(cb[i]);
      }
    }
    queueFlush();
  }
  function flushPreFlushCbs(instance, minUid, seen, i = flushIndex + 1) {
    if (true) {
      seen = seen || /* @__PURE__ */ new Map();
    }
    for (; i < queue.length; i++) {
      const cb = queue[i];
      if (cb && cb.flags & 2) {
        if (instance && cb.id !== instance.uid || minUid !== void 0 && !(cb.id >= minUid)) {
          continue;
        }
        if (checkRecursiveUpdates(seen, cb)) {
          continue;
        }
        queue.splice(i, 1);
        i = instance ? i - 1 : flushIndex;
        if (cb.flags & 4) {
          cb.flags &= -2;
        }
        cb();
        if (!(cb.flags & 4)) {
          cb.flags &= -2;
        }
      }
    }
  }
  function flushPostFlushCbs(seen, skip, minUid = 0) {
    if (pendingPostFlushCbs.length) {
      let cbs = pendingPostFlushCbs;
      let kept = 0;
      if (skip) {
        cbs = [];
        for (let i = 0; i < pendingPostFlushCbs.length; i++) {
          const cb = pendingPostFlushCbs[i];
          if (cb.i !== void 0 ? !cb.i || cb.i.uid < minUid : skip.has(cb)) {
            pendingPostFlushCbs[kept++] = cb;
          } else {
            cbs.push(cb);
          }
        }
      }
      const deduped = [...new Set(cbs)].sort((a, b) => getId(a) - getId(b));
      pendingPostFlushCbs.length = kept;
      if (activePostFlushCbs) {
        for (let i = 0; i < deduped.length; i++) {
          activePostFlushCbs.push(deduped[i]);
        }
        return;
      }
      activePostFlushCbs = deduped;
      if (true) {
        seen = seen || /* @__PURE__ */ new Map();
      }
      for (postFlushIndex = 0; postFlushIndex < activePostFlushCbs.length; postFlushIndex++) {
        const cb = activePostFlushCbs[postFlushIndex];
        if (checkRecursiveUpdates(seen, cb)) {
          continue;
        }
        if (cb.flags & 4) {
          cb.flags &= -2;
        }
        if (!(cb.flags & 8)) cb();
        cb.flags &= -2;
      }
      activePostFlushCbs = null;
      postFlushIndex = 0;
    }
  }
  var isFlushing = false;
  function flushOnAppMount(fn, minUid) {
    if (isFlushing) {
      fn();
      return;
    }
    const queued = minUid === void 0 ? void 0 : new Set(pendingPostFlushCbs);
    fn();
    isFlushing = true;
    try {
      flushPreFlushCbs(void 0, minUid);
      flushPostFlushCbs(void 0, queued, minUid);
    } finally {
      isFlushing = false;
    }
  }
  var getId = (job) => job.id == null ? job.flags & 2 ? -1 : Infinity : job.id;
  function flushJobs(seen) {
    if (true) {
      seen = seen || /* @__PURE__ */ new Map();
    }
    const check = true ? (job) => checkRecursiveUpdates(seen, job) : NOOP;
    try {
      for (flushIndex = 0; flushIndex < queue.length; flushIndex++) {
        const job = queue[flushIndex];
        if (job && !(job.flags & 8)) {
          if (check(job)) {
            continue;
          }
          if (job.flags & 4) {
            job.flags &= ~1;
          }
          callWithErrorHandling(
            job,
            job.i,
            job.i ? 15 : 14
          );
          if (!(job.flags & 4)) {
            job.flags &= ~1;
          }
        }
      }
    } finally {
      for (; flushIndex < queue.length; flushIndex++) {
        const job = queue[flushIndex];
        if (job) {
          job.flags &= -2;
        }
      }
      flushIndex = -1;
      queue.length = 0;
      flushPostFlushCbs(seen);
      currentFlushPromise = null;
      if (queue.length || pendingPostFlushCbs.length) {
        flushJobs(seen);
      }
    }
  }
  function checkRecursiveUpdates(seen, fn) {
    const count = seen.get(fn) || 0;
    if (count > RECURSION_LIMIT) {
      const instance = fn.i;
      const componentName = instance && getComponentName(instance.type);
      handleError(
        `Maximum recursive updates exceeded${componentName ? ` in component <${componentName}>` : ``}. This means you have a reactive effect that is mutating its own dependencies and thus recursively triggering itself. Possible sources include component template, render function, updated hook or watcher source function.`,
        null,
        10
      );
      return true;
    }
    seen.set(fn, count + 1);
    return false;
  }
  var isHmrUpdating = false;
  var setHmrUpdating = (v) => {
    try {
      return isHmrUpdating;
    } finally {
      isHmrUpdating = v;
    }
  };
  var hmrDirtyComponents = /* @__PURE__ */ new Map();
  if (true) {
    getGlobalThis().__VUE_HMR_RUNTIME__ = {
      createRecord: tryWrap(createRecord),
      rerender: tryWrap(rerender),
      reload: tryWrap(reload)
    };
  }
  var map = /* @__PURE__ */ new Map();
  function registerHMR(instance) {
    const id2 = instance.type.__hmrId;
    let record = map.get(id2);
    if (!record) {
      createRecord(id2, instance.type);
      record = map.get(id2);
    }
    record.instances.add(instance);
  }
  function unregisterHMR(instance) {
    map.get(instance.type.__hmrId).instances.delete(instance);
  }
  function createRecord(id2, initialDef) {
    if (map.has(id2)) {
      return false;
    }
    map.set(id2, {
      initialDef: normalizeClassComponent(initialDef),
      instances: /* @__PURE__ */ new Set()
    });
    return true;
  }
  function normalizeClassComponent(component) {
    return isClassComponent(component) ? component.__vccOpts : component;
  }
  function rerender(id2, newRender) {
    const record = map.get(id2);
    if (!record) {
      return;
    }
    record.initialDef.render = newRender;
    [...record.instances].forEach((instance) => {
      if (newRender) {
        instance.render = newRender;
        normalizeClassComponent(instance.type).render = newRender;
      }
      instance.renderCache = [];
      isHmrUpdating = true;
      if (!(instance.job.flags & 8)) {
        instance.update();
      }
      isHmrUpdating = false;
    });
  }
  function reload(id2, newComp) {
    const record = map.get(id2);
    if (!record) return;
    newComp = normalizeClassComponent(newComp);
    updateComponentDef(record.initialDef, newComp);
    const instances = [...record.instances];
    for (let i = 0; i < instances.length; i++) {
      const instance = instances[i];
      const oldComp = normalizeClassComponent(instance.type);
      let dirtyInstances = hmrDirtyComponents.get(oldComp);
      if (!dirtyInstances) {
        if (oldComp !== record.initialDef) {
          updateComponentDef(oldComp, newComp);
        }
        hmrDirtyComponents.set(oldComp, dirtyInstances = /* @__PURE__ */ new Set());
      }
      dirtyInstances.add(instance);
      instance.appContext.propsCache.delete(instance.type);
      instance.appContext.emitsCache.delete(instance.type);
      instance.appContext.optionsCache.delete(instance.type);
      if (instance.ceReload) {
        dirtyInstances.add(instance);
        instance.ceReload(newComp.styles);
        dirtyInstances.delete(instance);
      } else if (instance.parent) {
        queueJob(() => {
          if (!(instance.job.flags & 8)) {
            isHmrUpdating = true;
            instance.parent.update();
            isHmrUpdating = false;
            dirtyInstances.delete(instance);
          }
        });
      } else if (instance.appContext.reload) {
        instance.appContext.reload();
      } else if (typeof window !== "undefined") {
        window.location.reload();
      } else {
        console.warn(
          "[HMR] Root or manually mounted instance modified. Full reload required."
        );
      }
      if (instance.root.ce && instance !== instance.root) {
        instance.root.ce._removeChildStyle(oldComp);
      }
    }
    queuePostFlushCb(() => {
      hmrDirtyComponents.clear();
    });
  }
  function updateComponentDef(oldComp, newComp) {
    extend(oldComp, newComp);
    for (const key in oldComp) {
      if (key !== "__file" && !(key in newComp)) {
        delete oldComp[key];
      }
    }
  }
  function tryWrap(fn) {
    return (id2, arg) => {
      try {
        return fn(id2, arg);
      } catch (e) {
        console.error(e);
        console.warn(
          `[HMR] Something went wrong during Vue component hot-reload. Full reload required.`
        );
      }
    };
  }
  var devtools$1;
  var buffer = [];
  var devtoolsNotInstalled = false;
  function emit$1(event, ...args) {
    if (devtools$1) {
      devtools$1.emit(event, ...args);
    } else if (!devtoolsNotInstalled) {
      buffer.push({ event, args });
    }
  }
  function setDevtoolsHook$1(hook, target) {
    var _a, _b;
    devtools$1 = hook;
    if (devtools$1) {
      devtools$1.enabled = true;
      buffer.forEach(({ event, args }) => devtools$1.emit(event, ...args));
      buffer = [];
    } else if (
      // handle late devtools injection - only do this if we are in an actual
      // browser environment to avoid the timer handle stalling test runner exit
      // (#4815)
      typeof window !== "undefined" && // some envs mock window but not fully
      window.HTMLElement && // also exclude jsdom
      // eslint-disable-next-line no-restricted-syntax
      !((_b = (_a = window.navigator) == null ? void 0 : _a.userAgent) == null ? void 0 : _b.includes("jsdom"))
    ) {
      const replay = target.__VUE_DEVTOOLS_HOOK_REPLAY__ = target.__VUE_DEVTOOLS_HOOK_REPLAY__ || [];
      replay.push((newHook) => {
        setDevtoolsHook$1(newHook, target);
      });
      setTimeout(() => {
        if (!devtools$1) {
          target.__VUE_DEVTOOLS_HOOK_REPLAY__ = null;
          devtoolsNotInstalled = true;
          buffer = [];
        }
      }, 3e3);
    } else {
      devtoolsNotInstalled = true;
      buffer = [];
    }
  }
  function devtoolsInitApp(app, version2) {
    emit$1("app:init", app, version2, {
      Fragment,
      Text,
      Comment,
      Static
    });
  }
  function devtoolsUnmountApp(app) {
    emit$1("app:unmount", app);
  }
  var devtoolsComponentAdded = /* @__PURE__ */ createDevtoolsComponentHook(
    "component:added"
    /* COMPONENT_ADDED */
  );
  var devtoolsComponentUpdated = /* @__PURE__ */ createDevtoolsComponentHook(
    "component:updated"
    /* COMPONENT_UPDATED */
  );
  var _devtoolsComponentRemoved = /* @__PURE__ */ createDevtoolsComponentHook(
    "component:removed"
    /* COMPONENT_REMOVED */
  );
  var devtoolsComponentRemoved = (component) => {
    if (devtools$1 && typeof devtools$1.cleanupBuffer === "function" && // remove the component if it wasn't buffered
    !devtools$1.cleanupBuffer(component)) {
      _devtoolsComponentRemoved(component);
    }
  };
  // @__NO_SIDE_EFFECTS__
  function createDevtoolsComponentHook(hook) {
    return (component) => {
      emit$1(
        hook,
        component.appContext.app,
        component.uid,
        component.parent ? component.parent.uid : void 0,
        component
      );
    };
  }
  var devtoolsPerfStart = /* @__PURE__ */ createDevtoolsPerformanceHook(
    "perf:start"
    /* PERFORMANCE_START */
  );
  var devtoolsPerfEnd = /* @__PURE__ */ createDevtoolsPerformanceHook(
    "perf:end"
    /* PERFORMANCE_END */
  );
  function createDevtoolsPerformanceHook(hook) {
    return (component, type, time) => {
      emit$1(hook, component.appContext.app, component.uid, component, type, time);
    };
  }
  function devtoolsComponentEmit(component, event, params) {
    emit$1(
      "component:emit",
      component.appContext.app,
      component,
      event,
      params
    );
  }
  var currentRenderingInstance = null;
  var currentScopeId = null;
  function setCurrentRenderingInstance(instance) {
    const prev = currentRenderingInstance;
    currentRenderingInstance = instance;
    currentScopeId = instance && instance.type.__scopeId || null;
    return prev;
  }
  function withCtx(fn, ctx = currentRenderingInstance, isNonScopedSlot) {
    if (!ctx) return fn;
    if (fn._n) {
      return fn;
    }
    const renderFnWithContext = (...args) => {
      if (renderFnWithContext._d) {
        setBlockTracking(-1);
      }
      const prevInstance = setCurrentRenderingInstance(ctx);
      const prevStackSize = blockStack.length;
      let res;
      try {
        res = fn(...args);
      } finally {
        for (let i = blockStack.length; i > prevStackSize; i--) closeBlock();
        setCurrentRenderingInstance(prevInstance);
        if (renderFnWithContext._d) {
          setBlockTracking(1);
        }
      }
      if (true) {
        devtoolsComponentUpdated(ctx);
      }
      return res;
    };
    renderFnWithContext._n = true;
    renderFnWithContext._c = true;
    renderFnWithContext._d = true;
    return renderFnWithContext;
  }
  function validateDirectiveName(name) {
    if (isBuiltInDirective(name)) {
      warn$1("Do not use built-in directive ids as custom directive id: " + name);
    }
  }
  function invokeDirectiveHook(vnode, prevVNode, instance, name) {
    const bindings = vnode.dirs;
    const oldBindings = prevVNode && prevVNode.dirs;
    for (let i = 0; i < bindings.length; i++) {
      const binding = bindings[i];
      if (oldBindings) {
        binding.oldValue = oldBindings[i].value;
      }
      let hook = binding.dir[name];
      if (hook) {
        pauseTracking();
        callWithAsyncErrorHandling(hook, instance, 8, [
          vnode.el,
          binding,
          vnode,
          prevVNode
        ]);
        resetTracking();
      }
    }
  }
  function provide(key, value) {
    if (true) {
      if (!currentInstance || currentInstance.isMounted) {
        warn$1(`provide() can only be used inside setup().`);
      }
    }
    if (currentInstance) {
      let provides = currentInstance.provides;
      const parentProvides = currentInstance.parent && currentInstance.parent.provides;
      if (parentProvides === provides) {
        provides = currentInstance.provides = Object.create(parentProvides);
      }
      provides[key] = value;
    }
  }
  function inject(key, defaultValue, treatDefaultAsFactory = false) {
    const instance = getCurrentInstance();
    if (instance || currentApp) {
      let provides = currentApp ? currentApp._context.provides : instance ? instance.parent == null || instance.ce ? instance.vnode.appContext && instance.vnode.appContext.provides : instance.parent.provides : void 0;
      if (provides && key in provides) {
        return provides[key];
      } else if (arguments.length > 1) {
        return treatDefaultAsFactory && isFunction(defaultValue) ? defaultValue.call(instance && instance.proxy) : defaultValue;
      } else if (true) {
        warn$1(`injection "${String(key)}" not found.`);
      }
    } else if (true) {
      warn$1(`inject() can only be used inside setup() or functional components.`);
    }
  }
  var ssrContextKey = /* @__PURE__ */ Symbol.for("v-scx");
  var useSSRContext = () => {
    {
      const ctx = inject(ssrContextKey);
      if (!ctx) {
        warn$1(
          `Server rendering context not provided. Make sure to only call useSSRContext() conditionally in the server build.`
        );
      }
      return ctx;
    }
  };
  function watch2(source, cb, options) {
    if (!isFunction(cb)) {
      warn$1(
        `\`watch(fn, options?)\` signature has been moved to a separate API. Use \`watchEffect(fn, options?)\` instead. \`watch\` now only supports \`watch(source, cb, options?) signature.`
      );
    }
    return doWatch(source, cb, options);
  }
  function doWatch(source, cb, options = EMPTY_OBJ) {
    const { immediate, deep, flush, once } = options;
    if (!cb) {
      if (immediate !== void 0) {
        warn$1(
          `watch() "immediate" option is only respected when using the watch(source, callback, options?) signature.`
        );
      }
      if (deep !== void 0) {
        warn$1(
          `watch() "deep" option is only respected when using the watch(source, callback, options?) signature.`
        );
      }
      if (once !== void 0) {
        warn$1(
          `watch() "once" option is only respected when using the watch(source, callback, options?) signature.`
        );
      }
    }
    const baseWatchOptions = extend({}, options);
    if (true) baseWatchOptions.onWarn = warn$1;
    const runsImmediately = cb && immediate || !cb && flush !== "post";
    let ssrCleanup;
    if (isInSSRComponentSetup) {
      if (flush === "sync") {
        const ctx = useSSRContext();
        ssrCleanup = ctx.__watcherHandles || (ctx.__watcherHandles = []);
      } else if (!runsImmediately) {
        const watchStopHandle = () => {
        };
        watchStopHandle.stop = NOOP;
        watchStopHandle.resume = NOOP;
        watchStopHandle.pause = NOOP;
        return watchStopHandle;
      }
    }
    const instance = currentInstance;
    baseWatchOptions.call = (fn, type, args) => callWithAsyncErrorHandling(fn, instance, type, args);
    let isPre = false;
    if (flush === "post") {
      baseWatchOptions.scheduler = (job) => {
        job.i = instance;
        queuePostRenderEffect(job, instance && instance.suspense);
      };
    } else if (flush !== "sync") {
      isPre = true;
      baseWatchOptions.scheduler = (job, isFirstRun) => {
        if (isFirstRun) {
          job();
        } else {
          queueJob(job);
        }
      };
    }
    baseWatchOptions.augmentJob = (job) => {
      if (cb) {
        job.flags |= 4;
      }
      if (isPre) {
        job.flags |= 2;
        if (instance) {
          job.id = instance.uid;
          job.i = instance;
        }
      }
    };
    const watchHandle = watch(source, cb, baseWatchOptions);
    if (isInSSRComponentSetup) {
      if (ssrCleanup) {
        ssrCleanup.push(watchHandle);
      } else if (runsImmediately) {
        watchHandle();
      }
    }
    return watchHandle;
  }
  function instanceWatch(source, value, options) {
    const publicThis = this.proxy;
    const getter = isString(source) ? source.includes(".") ? createPathGetter(publicThis, source) : () => publicThis[source] : source.bind(publicThis, publicThis);
    let cb;
    if (isFunction(value)) {
      cb = value;
    } else {
      cb = value.handler;
      options = value;
    }
    const reset = setCurrentInstance(this);
    const res = doWatch(getter, cb.bind(publicThis), options);
    reset();
    return res;
  }
  function createPathGetter(ctx, path) {
    const segments = path.split(".");
    return () => {
      let cur = ctx;
      for (let i = 0; i < segments.length && cur; i++) {
        cur = cur[segments[i]];
      }
      return cur;
    };
  }
  var pendingMounts = /* @__PURE__ */ new WeakMap();
  var TeleportEndKey = /* @__PURE__ */ Symbol("_vte");
  var isTeleport = (type) => type.__isTeleport;
  var isTeleportDisabled = (props) => props && (props.disabled || props.disabled === "");
  var isTeleportDeferred = (props) => props && (props.defer || props.defer === "");
  var isTargetSVG = (target) => typeof SVGElement !== "undefined" && target instanceof SVGElement;
  var isTargetMathML = (target) => typeof MathMLElement === "function" && target instanceof MathMLElement;
  var resolveTarget = (props, select2) => {
    const targetSelector = props && props.to;
    if (isString(targetSelector)) {
      if (!select2) {
        warn$1(
          `Current renderer does not support string target for Teleports. (missing querySelector renderer option)`
        );
        return null;
      } else {
        const target = select2(targetSelector);
        if (!target && !isTeleportDisabled(props)) {
          warn$1(
            `Failed to locate Teleport target with selector "${targetSelector}". Note the target element must exist before the component is mounted - i.e. the target cannot be rendered by the component itself, and ideally should be outside of the entire Vue component tree.`
          );
        }
        return target;
      }
    } else {
      if (!targetSelector && !isTeleportDisabled(props)) {
        warn$1(`Invalid Teleport target: ${targetSelector}`);
      }
      return targetSelector;
    }
  };
  var TeleportImpl = {
    name: "Teleport",
    __isTeleport: true,
    process(n1, n2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized, internals) {
      const {
        mc: mountChildren,
        pc: patchChildren,
        pbc: patchBlockChildren,
        o: { insert, querySelector, createText, createComment, parentNode }
      } = internals;
      const disabled = isTeleportDisabled(n2.props);
      let { dynamicChildren } = n2;
      if (isHmrUpdating) {
        optimized = false;
        dynamicChildren = null;
      }
      const mount2 = (vnode, container2, anchor2) => {
        if (vnode.shapeFlag & 16) {
          mountChildren(
            vnode.children,
            container2,
            anchor2,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
        }
      };
      const mountToTarget = (vnode = n2) => {
        const disabled2 = isTeleportDisabled(vnode.props);
        const target = vnode.target = resolveTarget(vnode.props, querySelector);
        const targetAnchor = prepareAnchor(target, vnode, createText, insert);
        if (target) {
          if (namespace2 !== "svg" && isTargetSVG(target)) {
            namespace2 = "svg";
          } else if (namespace2 !== "mathml" && isTargetMathML(target)) {
            namespace2 = "mathml";
          }
          if (parentComponent && parentComponent.isCE) {
            (parentComponent.ce._teleportTargets || (parentComponent.ce._teleportTargets = /* @__PURE__ */ new Set())).add(target);
          }
          if (!disabled2) {
            mount2(vnode, target, targetAnchor);
            updateCssVars(vnode, false);
          }
        } else if (!disabled2) {
          warn$1("Invalid Teleport target on mount:", target, `(${typeof target})`);
        }
      };
      const queuePendingMount = (vnode) => {
        const mountJob = () => {
          if (pendingMounts.get(vnode) !== mountJob) return;
          pendingMounts.delete(vnode);
          if (isTeleportDisabled(vnode.props)) {
            const mountContainer = parentNode(vnode.el) || container;
            mount2(vnode, mountContainer, vnode.anchor);
            updateCssVars(vnode, true);
          }
          mountToTarget(vnode);
        };
        pendingMounts.set(vnode, mountJob);
        queuePostRenderEffect(mountJob, parentSuspense);
      };
      if (n1 == null) {
        const placeholder = n2.el = true ? createComment("teleport start") : createText("");
        const mainAnchor = n2.anchor = true ? createComment("teleport end") : createText("");
        insert(placeholder, container, anchor);
        insert(mainAnchor, container, anchor);
        if (isTeleportDeferred(n2.props) || parentSuspense && parentSuspense.pendingBranch) {
          queuePendingMount(n2);
          return;
        }
        if (disabled) {
          mount2(n2, container, mainAnchor);
          updateCssVars(n2, true);
        }
        mountToTarget();
      } else {
        n2.el = n1.el;
        const mainAnchor = n2.anchor = n1.anchor;
        const pendingMount = pendingMounts.get(n1);
        if (pendingMount) {
          pendingMount.flags |= 8;
          pendingMounts.delete(n1);
          queuePendingMount(n2);
          return;
        }
        n2.targetStart = n1.targetStart;
        const target = n2.target = n1.target;
        const targetAnchor = n2.targetAnchor = n1.targetAnchor;
        const wasDisabled = isTeleportDisabled(n1.props);
        const currentContainer = wasDisabled ? container : target;
        const currentAnchor = wasDisabled ? mainAnchor : targetAnchor;
        if (namespace2 === "svg" || isTargetSVG(target)) {
          namespace2 = "svg";
        } else if (namespace2 === "mathml" || isTargetMathML(target)) {
          namespace2 = "mathml";
        }
        if (dynamicChildren) {
          patchBlockChildren(
            n1.dynamicChildren,
            dynamicChildren,
            currentContainer,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds
          );
          traverseStaticChildren(n1, n2, false);
        } else if (!optimized) {
          patchChildren(
            n1,
            n2,
            currentContainer,
            currentAnchor,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            false
          );
        }
        if (disabled) {
          if (!wasDisabled) {
            moveTeleport(
              n2,
              container,
              mainAnchor,
              internals,
              1
            );
          } else {
            if (n2.props && n1.props && n2.props.to !== n1.props.to) {
              n2.props.to = n1.props.to;
            }
          }
        } else {
          if ((n2.props && n2.props.to) !== (n1.props && n1.props.to)) {
            const nextTarget = resolveTarget(n2.props, querySelector);
            if (nextTarget) {
              n2.target = nextTarget;
              moveTeleport(
                n2,
                nextTarget,
                null,
                internals,
                0
              );
            } else if (true) {
              warn$1(
                "Invalid Teleport target on update:",
                target,
                `(${typeof target})`
              );
            }
          } else if (wasDisabled) {
            moveTeleport(
              n2,
              target,
              targetAnchor,
              internals,
              1
            );
          }
        }
        updateCssVars(n2, disabled);
      }
    },
    remove(vnode, parentComponent, parentSuspense, { um: unmount, o: { remove: hostRemove } }, doRemove) {
      const {
        shapeFlag,
        children: children2,
        anchor,
        targetStart,
        targetAnchor,
        target,
        props
      } = vnode;
      const disabled = isTeleportDisabled(props);
      const shouldRemove = doRemove || !disabled;
      const pendingMount = pendingMounts.get(vnode);
      if (pendingMount) {
        pendingMount.flags |= 8;
        pendingMounts.delete(vnode);
      }
      if (target) {
        hostRemove(targetStart);
        hostRemove(targetAnchor);
      }
      doRemove && hostRemove(anchor);
      if (!pendingMount && (disabled || target) && shapeFlag & 16) {
        for (let i = 0; i < children2.length; i++) {
          const child = children2[i];
          unmount(
            child,
            parentComponent,
            parentSuspense,
            shouldRemove,
            !!child.dynamicChildren
          );
        }
      }
    },
    move: moveTeleport,
    hydrate: hydrateTeleport
  };
  function moveTeleport(vnode, container, parentAnchor, { o: { insert }, m: move }, moveType = 2) {
    if (moveType === 0) {
      if (vnode.targetStart) {
        insert(vnode.targetStart, container, parentAnchor);
      }
      insert(vnode.targetAnchor, container, parentAnchor);
      parentAnchor = vnode.targetAnchor;
    }
    const { el, anchor, shapeFlag, children: children2, props } = vnode;
    const isReorder = moveType === 2;
    if (isReorder) {
      insert(el, container, parentAnchor);
    }
    if (!pendingMounts.has(vnode) && (!isReorder || isTeleportDisabled(props))) {
      if (shapeFlag & 16) {
        for (let i = 0; i < children2.length; i++) {
          move(
            children2[i],
            container,
            parentAnchor,
            2
          );
        }
      }
    }
    if (isReorder) {
      insert(anchor, container, parentAnchor);
    }
  }
  function hydrateTeleport(node, vnode, parentComponent, parentSuspense, slotScopeIds, optimized, {
    o: { nextSibling, parentNode, querySelector, insert, createText }
  }, hydrateChildren) {
    function hydrateAnchor(target2, targetNode) {
      let targetAnchor = targetNode;
      while (targetAnchor) {
        if (targetAnchor && targetAnchor.nodeType === 8) {
          if (targetAnchor.data === "teleport start anchor") {
            vnode.targetStart = targetAnchor;
          } else if (targetAnchor.data === "teleport anchor") {
            vnode.targetAnchor = targetAnchor;
            target2._lpa = vnode.targetAnchor && nextSibling(vnode.targetAnchor);
            break;
          }
        }
        targetAnchor = nextSibling(targetAnchor);
      }
    }
    function hydrateDisabledTeleport(node2, vnode2) {
      vnode2.anchor = hydrateChildren(
        nextSibling(node2),
        vnode2,
        parentNode(node2),
        parentComponent,
        parentSuspense,
        slotScopeIds,
        optimized
      );
    }
    const target = vnode.target = resolveTarget(
      vnode.props,
      querySelector
    );
    const disabled = isTeleportDisabled(vnode.props);
    if (target) {
      const targetNode = target._lpa || target.firstChild;
      if (vnode.shapeFlag & 16) {
        if (disabled) {
          hydrateDisabledTeleport(node, vnode);
          hydrateAnchor(target, targetNode);
          if (!vnode.targetAnchor) {
            prepareAnchor(
              target,
              vnode,
              createText,
              insert,
              // if target is the same as the main view, insert anchors before current node
              // to avoid hydrating mismatch
              parentNode(node) === target ? node : null
            );
          }
        } else {
          vnode.anchor = nextSibling(node);
          hydrateAnchor(target, targetNode);
          if (!vnode.targetAnchor) {
            prepareAnchor(target, vnode, createText, insert);
          }
          hydrateChildren(
            targetNode && nextSibling(targetNode),
            vnode,
            target,
            parentComponent,
            parentSuspense,
            slotScopeIds,
            optimized
          );
        }
      }
      updateCssVars(vnode, disabled);
    } else if (disabled) {
      if (vnode.shapeFlag & 16) {
        hydrateDisabledTeleport(node, vnode);
        vnode.targetStart = node;
        vnode.targetAnchor = nextSibling(node);
      }
    }
    return vnode.anchor && nextSibling(vnode.anchor);
  }
  var Teleport = TeleportImpl;
  function updateCssVars(vnode, isDisabled) {
    const ctx = vnode.ctx;
    if (ctx && ctx.ut) {
      let node, anchor;
      if (isDisabled) {
        node = vnode.el;
        anchor = vnode.anchor;
      } else {
        node = vnode.targetStart;
        anchor = vnode.targetAnchor;
      }
      while (node && node !== anchor) {
        if (node.nodeType === 1) node.setAttribute("data-v-owner", ctx.uid);
        node = node.nextSibling;
      }
      ctx.ut();
    }
  }
  function prepareAnchor(target, vnode, createText, insert, anchor = null) {
    const targetStart = vnode.targetStart = createText("");
    const targetAnchor = vnode.targetAnchor = createText("");
    targetStart[TeleportEndKey] = targetAnchor;
    if (target) {
      insert(targetStart, target, anchor);
      insert(targetAnchor, target, anchor);
    }
    return targetAnchor;
  }
  var leaveCbKey = /* @__PURE__ */ Symbol("_leaveCb");
  function findNonCommentChild(children2) {
    let child = children2[0];
    if (children2.length > 1) {
      let hasFound = false;
      for (const c of children2) {
        if (c.type !== Comment) {
          if (hasFound) {
            warn$1(
              "<transition> can only be used on a single element or component. Use <transition-group> for lists."
            );
            break;
          }
          child = c;
          hasFound = true;
          if (false) break;
        }
      }
    }
    return child;
  }
  function getInnerChild$1(vnode) {
    if (!isKeepAlive(vnode)) {
      if (isTeleport(vnode.type) && vnode.children) {
        return findNonCommentChild(vnode.children);
      }
      return vnode;
    }
    if (vnode.component) {
      return vnode.component.subTree;
    }
    const { shapeFlag, children: children2 } = vnode;
    if (children2) {
      if (shapeFlag & 16) {
        return children2[0];
      }
      if (shapeFlag & 32 && isFunction(children2.default)) {
        return children2.default();
      }
    }
  }
  function setTransitionHooks(vnode, hooks) {
    if (vnode.shapeFlag & 6 && vnode.component) {
      vnode.transition = hooks;
      const subTree = vnode.component.subTree;
      setTransitionHooks(
        isTeleport(subTree.type) ? getInnerChild$1(subTree) || subTree : subTree,
        hooks
      );
    } else if (vnode.shapeFlag & 128) {
      vnode.ssContent.transition = hooks.clone(vnode.ssContent);
      vnode.ssFallback.transition = hooks.clone(vnode.ssFallback);
    } else {
      vnode.transition = hooks;
      if (vnode.type === Fragment && isArray(vnode.children)) {
        const child = filterSingleRoot(vnode.children, false);
        if (child) {
          setTransitionHooks(child, hooks);
        }
      }
    }
  }
  function getTransitionRoot(vnode) {
    if (vnode.component) {
      return getTransitionRoot(vnode.component.subTree);
    }
    if (vnode.type === Fragment) {
      const child = isArray(vnode.children) && filterSingleRoot(vnode.children, false);
      return child ? getTransitionRoot(child) : void 0;
    }
    if (vnode.type !== Comment) {
      return vnode;
    }
  }
  // @__NO_SIDE_EFFECTS__
  function defineComponent(options, extraOptions) {
    return isFunction(options) ? (
      // #8236: extend call and options.name access are considered side-effects
      // by Rollup, so we have to wrap it in a pure-annotated IIFE.
      /* @__PURE__ */ (() => extend({ name: options.name }, extraOptions, { setup: options }))()
    ) : options;
  }
  function markAsyncBoundary(instance) {
    instance.ids = [instance.ids[0] + instance.ids[2]++ + "-", 0, 0];
  }
  var knownTemplateRefs = /* @__PURE__ */ new WeakSet();
  function isTemplateRefKey(refs, key) {
    let desc;
    return !!((desc = Object.getOwnPropertyDescriptor(refs, key)) && !desc.configurable);
  }
  var pendingSetRefMap = /* @__PURE__ */ new WeakMap();
  function setRef(rawRef, oldRawRef, parentSuspense, vnode, isUnmount = false) {
    if (isArray(rawRef)) {
      rawRef.forEach(
        (r, i) => setRef(
          r,
          oldRawRef && (isArray(oldRawRef) ? oldRawRef[i] : oldRawRef),
          parentSuspense,
          vnode,
          isUnmount
        )
      );
      return;
    }
    if (isAsyncWrapper(vnode) && !isUnmount) {
      if (vnode.shapeFlag & 512 && vnode.type.__asyncResolved && vnode.component.subTree.component) {
        setRef(rawRef, oldRawRef, parentSuspense, vnode.component.subTree);
      }
      return;
    }
    const refValue = vnode.shapeFlag & 4 ? getComponentPublicInstance(vnode.component) : vnode.el;
    const value = isUnmount ? null : refValue;
    const { i: owner, r: ref2 } = rawRef;
    if (!owner) {
      warn$1(
        `Missing ref owner context. ref cannot be used on hoisted vnodes. A vnode with ref must be created inside the render function.`
      );
      return;
    }
    const oldRef = oldRawRef && oldRawRef.r;
    const refs = owner.refs === EMPTY_OBJ ? owner.refs = {} : owner.refs;
    const setupState = owner.setupState;
    const rawSetupState = toRaw(setupState);
    const canSetSetupRef = setupState === EMPTY_OBJ ? NO : (key) => {
      if (true) {
        if (hasOwn(rawSetupState, key) && !isRef2(rawSetupState[key])) {
          warn$1(
            `Template ref "${key}" used on a non-ref value. It will not work in the production build.`
          );
        }
        if (knownTemplateRefs.has(rawSetupState[key])) {
          return false;
        }
      }
      if (isTemplateRefKey(refs, key)) {
        return false;
      }
      return hasOwn(rawSetupState, key);
    };
    const canSetRef = (ref22, key) => {
      if (knownTemplateRefs.has(ref22)) {
        return false;
      }
      if (key && isTemplateRefKey(refs, key)) {
        return false;
      }
      return true;
    };
    if (oldRef != null && oldRef !== ref2) {
      invalidatePendingSetRef(oldRawRef);
      if (isString(oldRef)) {
        refs[oldRef] = null;
        if (canSetSetupRef(oldRef)) {
          setupState[oldRef] = null;
        }
      } else if (isRef2(oldRef)) {
        const oldRawRefAtom = oldRawRef;
        if (canSetRef(oldRef, oldRawRefAtom.k)) {
          oldRef.value = null;
        }
        if (oldRawRefAtom.k) refs[oldRawRefAtom.k] = null;
      }
    }
    if (isFunction(ref2)) {
      callWithErrorHandling(ref2, owner, 12, [value, refs]);
    } else {
      const _isString = isString(ref2);
      const _isRef = isRef2(ref2);
      if (_isString || _isRef) {
        const doSet = () => {
          if (rawRef.f) {
            const existing = _isString ? canSetSetupRef(ref2) ? setupState[ref2] : refs[ref2] : canSetRef(ref2) || !rawRef.k ? ref2.value : refs[rawRef.k];
            if (isUnmount) {
              isArray(existing) && remove(existing, refValue);
            } else {
              if (!isArray(existing)) {
                if (_isString) {
                  refs[ref2] = [refValue];
                  if (canSetSetupRef(ref2)) {
                    setupState[ref2] = refs[ref2];
                  }
                } else {
                  const newVal = [refValue];
                  if (canSetRef(ref2, rawRef.k)) {
                    ref2.value = newVal;
                  }
                  if (rawRef.k) refs[rawRef.k] = newVal;
                }
              } else if (!existing.includes(refValue)) {
                existing.push(refValue);
              }
            }
          } else if (_isString) {
            refs[ref2] = value;
            if (canSetSetupRef(ref2)) {
              setupState[ref2] = value;
            }
          } else if (_isRef) {
            if (canSetRef(ref2, rawRef.k)) {
              ref2.value = value;
            }
            if (rawRef.k) refs[rawRef.k] = value;
          } else if (true) {
            warn$1("Invalid template ref type:", ref2, `(${typeof ref2})`);
          }
        };
        if (value) {
          const job = () => {
            doSet();
            pendingSetRefMap.delete(rawRef);
          };
          job.id = -1;
          pendingSetRefMap.set(rawRef, job);
          queuePostRenderEffect(job, parentSuspense);
        } else {
          invalidatePendingSetRef(rawRef);
          doSet();
        }
      } else if (true) {
        warn$1("Invalid template ref type:", ref2, `(${typeof ref2})`);
      }
    }
  }
  function invalidatePendingSetRef(rawRef) {
    const pendingSetRef = pendingSetRefMap.get(rawRef);
    if (pendingSetRef) {
      pendingSetRef.flags |= 8;
      pendingSetRefMap.delete(rawRef);
    }
  }
  var requestIdleCallback = getGlobalThis().requestIdleCallback || ((cb) => setTimeout(cb, 1));
  var cancelIdleCallback = getGlobalThis().cancelIdleCallback || ((id2) => clearTimeout(id2));
  var isAsyncWrapper = (i) => !!i.type.__asyncLoader;
  var isKeepAlive = (vnode) => vnode.type.__isKeepAlive;
  function onActivated(hook, target) {
    registerKeepAliveHook(hook, "a", target);
  }
  function onDeactivated(hook, target) {
    registerKeepAliveHook(hook, "da", target);
  }
  function registerKeepAliveHook(hook, type, target = currentInstance) {
    const wrappedHook = hook.__wdc || (hook.__wdc = () => {
      let current = target;
      while (current) {
        if (current.isDeactivated) {
          return;
        }
        current = current.parent;
      }
      return hook();
    });
    injectHook(type, wrappedHook, target);
    if (target) {
      let current = target.parent;
      while (current && current.parent) {
        if (isKeepAlive(current.parent.vnode)) {
          injectToKeepAliveRoot(wrappedHook, type, target, current);
        }
        current = current.parent;
      }
    }
  }
  function injectToKeepAliveRoot(hook, type, target, keepAliveRoot) {
    const injected = injectHook(
      type,
      hook,
      keepAliveRoot,
      true
      /* prepend */
    );
    onUnmounted(() => {
      remove(keepAliveRoot[type], injected);
    }, target);
  }
  function injectHook(type, hook, target = currentInstance, prepend = false) {
    if (target) {
      const hooks = target[type] || (target[type] = []);
      const wrappedHook = hook.__weh || (hook.__weh = (...args) => {
        pauseTracking();
        const reset = setCurrentInstance(target);
        const res = callWithAsyncErrorHandling(hook, target, type, args);
        reset();
        resetTracking();
        return res;
      });
      if (prepend) {
        hooks.unshift(wrappedHook);
      } else {
        hooks.push(wrappedHook);
      }
      return wrappedHook;
    } else if (true) {
      const apiName = toHandlerKey(ErrorTypeStrings$1[type].replace(/ hook$/, ""));
      warn$1(
        `${apiName} is called when there is no active component instance to be associated with. Lifecycle injection APIs can only be used during execution of setup(). If you are using async setup(), make sure to register lifecycle hooks before the first await statement.`
      );
    }
  }
  var createHook = (lifecycle) => (hook, target = currentInstance) => {
    if (!isInSSRComponentSetup || lifecycle === "sp") {
      injectHook(lifecycle, (...args) => hook(...args), target);
    }
  };
  var onBeforeMount = createHook("bm");
  var onMounted = createHook("m");
  var onBeforeUpdate = createHook(
    "bu"
  );
  var onUpdated = createHook("u");
  var onBeforeUnmount = createHook(
    "bum"
  );
  var onUnmounted = createHook("um");
  var onServerPrefetch = createHook(
    "sp"
  );
  var onRenderTriggered = createHook("rtg");
  var onRenderTracked = createHook("rtc");
  function onErrorCaptured(hook, target = currentInstance) {
    injectHook("ec", hook, target);
  }
  var COMPONENTS = "components";
  function resolveComponent(name, maybeSelfReference) {
    return resolveAsset(COMPONENTS, name, true, maybeSelfReference) || name;
  }
  var NULL_DYNAMIC_COMPONENT = /* @__PURE__ */ Symbol.for("v-ndc");
  function resolveDynamicComponent(component) {
    if (isString(component)) {
      return resolveAsset(COMPONENTS, component, false) || component;
    } else {
      return component || NULL_DYNAMIC_COMPONENT;
    }
  }
  function resolveAsset(type, name, warnMissing = true, maybeSelfReference = false) {
    const instance = currentRenderingInstance || currentInstance;
    if (instance) {
      const Component = instance.type;
      if (type === COMPONENTS) {
        const selfName = getComponentName(
          Component,
          false
        );
        if (selfName && (selfName === name || selfName === camelize(name) || selfName === capitalize(camelize(name)))) {
          return Component;
        }
      }
      const res = (
        // local registration
        // check instance[type] first which is resolved for options API
        resolve(instance[type] || Component[type], name) || // global registration
        resolve(instance.appContext[type], name)
      );
      if (!res && maybeSelfReference) {
        return Component;
      }
      if (warnMissing && !res) {
        const extra = type === COMPONENTS ? `
If this is a native custom element, make sure to exclude it from component resolution via compilerOptions.isCustomElement.` : ``;
        warn$1(`Failed to resolve ${type.slice(0, -1)}: ${name}${extra}`);
      }
      return res;
    } else if (true) {
      warn$1(
        `resolve${capitalize(type.slice(0, -1))} can only be used in render() or setup().`
      );
    }
  }
  function resolve(registry, name) {
    return registry && (registry[name] || registry[camelize(name)] || registry[capitalize(camelize(name))]);
  }
  function renderList(source, renderItem, cache, index) {
    let ret;
    const cached = cache && cache[index];
    const sourceIsArray = isArray(source);
    if (sourceIsArray || isString(source)) {
      const sourceIsReactiveArray = sourceIsArray && isReactive(source);
      let needsWrap = false;
      let isReadonlySource = false;
      if (sourceIsReactiveArray) {
        needsWrap = !isShallow(source);
        isReadonlySource = isReadonly(source);
        source = shallowReadArray(source);
      }
      ret = new Array(source.length);
      for (let i = 0, l = source.length; i < l; i++) {
        ret[i] = renderItem(
          needsWrap ? isReadonlySource ? toReadonly(toReactive(source[i])) : toReactive(source[i]) : source[i],
          i,
          void 0,
          cached && cached[i]
        );
      }
    } else if (typeof source === "number") {
      if (!Number.isInteger(source) || source < 0) {
        if (true) {
          warn$1(
            `The v-for range expects a positive integer value but got ${source}.`
          );
        }
        ret = [];
      } else {
        ret = new Array(source);
        for (let i = 0; i < source; i++) {
          ret[i] = renderItem(i + 1, i, void 0, cached && cached[i]);
        }
      }
    } else if (isObject(source)) {
      if (source[Symbol.iterator]) {
        ret = Array.from(
          source,
          (item, i) => renderItem(item, i, void 0, cached && cached[i])
        );
      } else {
        const keys = Object.keys(source);
        ret = new Array(keys.length);
        for (let i = 0, l = keys.length; i < l; i++) {
          const key = keys[i];
          ret[i] = renderItem(source[key], key, i, cached && cached[i]);
        }
      }
    } else {
      ret = [];
    }
    if (cache) {
      cache[index] = ret;
    }
    return ret;
  }
  function renderSlot(slots, name, props, fallback, noSlotted, branchKey) {
    if (props == null) props = {};
    if (currentRenderingInstance.ce || currentRenderingInstance.parent && isAsyncWrapper(currentRenderingInstance.parent) && currentRenderingInstance.parent.ce) {
      const slotProps = branchKey != null && props.key == null ? extend({}, props, { key: branchKey }) : props;
      const hasProps = Object.keys(slotProps).length > 0;
      if (name !== "default") slotProps.name = name;
      return openBlock(), createBlock(
        Fragment,
        null,
        [createVNode("slot", slotProps, fallback && fallback())],
        hasProps ? -2 : 64
      );
    }
    let slot = slots[name];
    if (slot && slot.length > 1) {
      warn$1(
        `SSR-optimized slot function detected in a non-SSR-optimized render function. You need to mark this component with $dynamic-slots in the parent template.`
      );
      slot = () => [];
    }
    if (slot && slot._c) {
      slot._d = false;
    }
    const prevStackSize = blockStack.length;
    openBlock();
    let rendered;
    try {
      const validSlotContent = slot && ensureValidVNode(slot(props));
      const slotKey = props.key || branchKey || // slot content array of a dynamic conditional slot may have a branch
      // key attached in the `createSlots` helper, respect that
      validSlotContent && validSlotContent.key;
      rendered = createBlock(
        Fragment,
        {
          key: (slotKey && !isSymbol(slotKey) ? slotKey : `_${name}`) + // #7256 force differentiate fallback content from actual content
          (!validSlotContent && fallback ? "_fb" : "")
        },
        validSlotContent || (fallback ? fallback() : []),
        validSlotContent && slots._ === 1 ? 64 : -2
      );
    } catch (err) {
      for (let i = blockStack.length; i > prevStackSize; i--) closeBlock();
      throw err;
    } finally {
      if (slot && slot._c) {
        slot._d = true;
      }
    }
    if (!noSlotted && rendered.scopeId) {
      rendered.slotScopeIds = [rendered.scopeId + "-s"];
    }
    return rendered;
  }
  function ensureValidVNode(vnodes) {
    return vnodes.some((child) => {
      if (!isVNode(child)) return true;
      if (child.type === Comment) return false;
      if (child.type === Fragment && !ensureValidVNode(child.children))
        return false;
      return true;
    }) ? vnodes : null;
  }
  var getPublicInstance = (i) => {
    if (!i) return null;
    if (isStatefulComponent(i)) return getComponentPublicInstance(i);
    return getPublicInstance(i.parent);
  };
  var resolveDevRootEl = (vnode) => {
    let found = false;
    while (true) {
      if (vnode.patchFlag > 0 && vnode.patchFlag & 2048) {
        const root2 = filterSingleRoot(vnode.children);
        if (!root2) {
          return;
        }
        vnode = root2;
        found = true;
        continue;
      }
      const component = vnode.component;
      if (component && component.subTree) {
        vnode = component.subTree;
        continue;
      }
      const suspense = vnode.suspense;
      if (suspense && suspense.activeBranch) {
        vnode = suspense.activeBranch;
        continue;
      }
      return found ? vnode.el : void 0;
    }
  };
  var getDevRootFragmentEl = (i) => {
    const el = i.subTree && resolveDevRootEl(i.subTree);
    return el === void 0 ? i.vnode.el : el;
  };
  var publicPropertiesMap = (
    // Move PURE marker to new line to workaround compiler discarding it
    // due to type annotation
    /* @__PURE__ */ extend(/* @__PURE__ */ Object.create(null), {
      $: (i) => i,
      $el: (i) => true ? getDevRootFragmentEl(i) : i.vnode.el,
      $data: (i) => i.data,
      $props: (i) => true ? shallowReadonly(i.props) : i.props,
      $attrs: (i) => true ? shallowReadonly(i.attrs) : i.attrs,
      $slots: (i) => true ? shallowReadonly(i.slots) : i.slots,
      $refs: (i) => true ? shallowReadonly(i.refs) : i.refs,
      $parent: (i) => getPublicInstance(i.parent),
      $root: (i) => getPublicInstance(i.root),
      $host: (i) => i.ce,
      $emit: (i) => i.emit,
      $options: (i) => true ? resolveMergedOptions(i) : i.type,
      $forceUpdate: (i) => i.f || (i.f = () => {
        queueJob(i.update);
      }),
      $nextTick: (i) => i.n || (i.n = nextTick.bind(i.proxy)),
      $watch: (i) => true ? instanceWatch.bind(i) : NOOP
    })
  );
  var isReservedPrefix = (key) => key === "_" || key === "$";
  var hasSetupBinding = (state, key) => state !== EMPTY_OBJ && !state.__isScriptSetup && hasOwn(state, key);
  var PublicInstanceProxyHandlers = {
    get({ _: instance }, key) {
      if (key === "__v_skip") {
        return true;
      }
      const { ctx, setupState, data, props, accessCache, type, appContext } = instance;
      if (key === "__isVue") {
        return true;
      }
      if (key[0] !== "$") {
        const n = accessCache[key];
        if (n !== void 0) {
          switch (n) {
            case 1:
              return setupState[key];
            case 2:
              return data[key];
            case 4:
              return ctx[key];
            case 3:
              return props[key];
          }
        } else if (hasSetupBinding(setupState, key)) {
          accessCache[key] = 1;
          return setupState[key];
        } else if (data !== EMPTY_OBJ && hasOwn(data, key)) {
          accessCache[key] = 2;
          return data[key];
        } else if (hasOwn(props, key)) {
          accessCache[key] = 3;
          return props[key];
        } else if (ctx !== EMPTY_OBJ && hasOwn(ctx, key)) {
          accessCache[key] = 4;
          return ctx[key];
        } else if (shouldCacheAccess) {
          accessCache[key] = 0;
        }
      }
      const publicGetter = publicPropertiesMap[key];
      let cssModule, globalProperties;
      if (publicGetter) {
        if (key === "$attrs") {
          track(instance.attrs, "get", "");
          markAttrsAccessed();
        } else if (key === "$slots") {
          track(instance, "get", key);
        }
        return publicGetter(instance);
      } else if (
        // css module (injected by vue-loader)
        (cssModule = type.__cssModules) && (cssModule = cssModule[key])
      ) {
        return cssModule;
      } else if (ctx !== EMPTY_OBJ && hasOwn(ctx, key)) {
        accessCache[key] = 4;
        return ctx[key];
      } else if (
        // global properties
        globalProperties = appContext.config.globalProperties, hasOwn(globalProperties, key)
      ) {
        {
          return globalProperties[key];
        }
      } else if (currentRenderingInstance && (!isString(key) || // #1091 avoid internal isRef/isVNode checks on component instance leading
      // to infinite warning loop
      key.indexOf("__v") !== 0)) {
        if (data !== EMPTY_OBJ && isReservedPrefix(key[0]) && hasOwn(data, key)) {
          warn$1(
            `Property ${JSON.stringify(
              key
            )} must be accessed via $data because it starts with a reserved character ("$" or "_") and is not proxied on the render context.`
          );
        } else if (instance === currentRenderingInstance) {
          warn$1(
            `Property ${JSON.stringify(key)} was accessed during render but is not defined on instance.`
          );
        }
      }
    },
    set({ _: instance }, key, value) {
      const { data, setupState, ctx } = instance;
      if (hasSetupBinding(setupState, key)) {
        setupState[key] = value;
        return true;
      } else if (setupState.__isScriptSetup && hasOwn(setupState, key)) {
        warn$1(`Cannot mutate <script setup> binding "${key}" from Options API.`);
        return false;
      } else if (data !== EMPTY_OBJ && hasOwn(data, key)) {
        data[key] = value;
        return true;
      } else if (hasOwn(instance.props, key)) {
        warn$1(`Attempting to mutate prop "${key}". Props are readonly.`);
        return false;
      }
      if (key[0] === "$" && key.slice(1) in instance) {
        warn$1(
          `Attempting to mutate public property "${key}". Properties starting with $ are reserved and readonly.`
        );
        return false;
      } else {
        if (key in instance.appContext.config.globalProperties) {
          Object.defineProperty(ctx, key, {
            enumerable: true,
            configurable: true,
            value
          });
        } else {
          ctx[key] = value;
        }
      }
      return true;
    },
    has({
      _: { data, setupState, accessCache, ctx, appContext, props, type }
    }, key) {
      let cssModules;
      return !!(accessCache[key] || data !== EMPTY_OBJ && key[0] !== "$" && hasOwn(data, key) || hasSetupBinding(setupState, key) || hasOwn(props, key) || hasOwn(ctx, key) || hasOwn(publicPropertiesMap, key) || hasOwn(appContext.config.globalProperties, key) || (cssModules = type.__cssModules) && cssModules[key]);
    },
    defineProperty(target, key, descriptor) {
      if (descriptor.get != null) {
        target._.accessCache[key] = 0;
      } else if (hasOwn(descriptor, "value")) {
        this.set(target, key, descriptor.value, null);
      }
      return Reflect.defineProperty(target, key, descriptor);
    }
  };
  if (true) {
    PublicInstanceProxyHandlers.ownKeys = (target) => {
      warn$1(
        `Avoid app logic that relies on enumerating keys on a component instance. The keys will be empty in production mode to avoid performance overhead.`
      );
      return Reflect.ownKeys(target);
    };
  }
  function createDevRenderContext(instance) {
    const target = {};
    Object.defineProperty(target, `_`, {
      configurable: true,
      enumerable: false,
      get: () => instance
    });
    Object.keys(publicPropertiesMap).forEach((key) => {
      Object.defineProperty(target, key, {
        configurable: true,
        enumerable: false,
        get: () => publicPropertiesMap[key](instance),
        // intercepted by the proxy so no need for implementation,
        // but needed to prevent set errors
        set: NOOP
      });
    });
    return target;
  }
  function exposePropsOnRenderContext(instance) {
    const {
      ctx,
      propsOptions: [propsOptions]
    } = instance;
    if (propsOptions) {
      Object.keys(propsOptions).forEach((key) => {
        Object.defineProperty(ctx, key, {
          enumerable: true,
          configurable: true,
          get: () => instance.props[key],
          set: NOOP
        });
      });
    }
  }
  function exposeSetupStateOnRenderContext(instance) {
    const { ctx, setupState } = instance;
    Object.keys(toRaw(setupState)).forEach((key) => {
      if (!setupState.__isScriptSetup) {
        if (isReservedPrefix(key[0])) {
          warn$1(
            `setup() return property ${JSON.stringify(
              key
            )} should not start with "$" or "_" which are reserved prefixes for Vue internals.`
          );
          return;
        }
        Object.defineProperty(ctx, key, {
          enumerable: true,
          configurable: true,
          get: () => setupState[key],
          set: NOOP
        });
      }
    });
  }
  function useSlots() {
    return getContext("useSlots").slots;
  }
  function useAttrs() {
    return getContext("useAttrs").attrs;
  }
  function getContext(calledFunctionName) {
    const i = getCurrentInstance();
    if (!i) {
      warn$1(`${calledFunctionName}() called without active instance.`);
    }
    return i.setupContext || (i.setupContext = createSetupContext(i));
  }
  function normalizePropsOrEmits(props) {
    return isArray(props) ? props.reduce(
      (normalized, p2) => (normalized[p2] = null, normalized),
      {}
    ) : props;
  }
  function createPropsRestProxy(props, excludedKeys) {
    const ret = {};
    for (const key in props) {
      if (!excludedKeys.includes(key)) {
        Object.defineProperty(ret, key, {
          enumerable: true,
          get: () => props[key]
        });
      }
    }
    return ret;
  }
  function createDuplicateChecker() {
    const cache = /* @__PURE__ */ Object.create(null);
    return (type, key) => {
      if (cache[key]) {
        warn$1(`${type} property "${key}" is already defined in ${cache[key]}.`);
      } else {
        cache[key] = type;
      }
    };
  }
  var shouldCacheAccess = true;
  function applyOptions(instance) {
    const options = resolveMergedOptions(instance);
    const publicThis = instance.proxy;
    const ctx = instance.ctx;
    shouldCacheAccess = false;
    if (options.beforeCreate) {
      callHook(options.beforeCreate, instance, "bc");
    }
    const {
      // state
      data: dataOptions,
      computed: computedOptions,
      methods,
      watch: watchOptions,
      provide: provideOptions,
      inject: injectOptions,
      // lifecycle
      created,
      beforeMount,
      mounted,
      beforeUpdate,
      updated,
      activated,
      deactivated,
      beforeDestroy,
      beforeUnmount,
      destroyed,
      unmounted,
      render: render2,
      renderTracked,
      renderTriggered,
      errorCaptured,
      serverPrefetch,
      // public API
      expose,
      inheritAttrs,
      // assets
      components,
      directives,
      filters
    } = options;
    const checkDuplicateProperties = true ? createDuplicateChecker() : null;
    if (true) {
      const [propsOptions] = instance.propsOptions;
      if (propsOptions) {
        for (const key in propsOptions) {
          checkDuplicateProperties("Props", key);
        }
      }
    }
    if (injectOptions) {
      resolveInjections(injectOptions, ctx, checkDuplicateProperties);
    }
    if (methods) {
      for (const key in methods) {
        const methodHandler = methods[key];
        if (isFunction(methodHandler)) {
          if (true) {
            Object.defineProperty(ctx, key, {
              value: methodHandler.bind(publicThis),
              configurable: true,
              enumerable: true,
              writable: true
            });
          } else {
            ctx[key] = methodHandler.bind(publicThis);
          }
          if (true) {
            checkDuplicateProperties("Methods", key);
          }
        } else if (true) {
          warn$1(
            `Method "${key}" has type "${typeof methodHandler}" in the component definition. Did you reference the function correctly?`
          );
        }
      }
    }
    if (dataOptions) {
      if (!isFunction(dataOptions)) {
        warn$1(
          `The data option must be a function. Plain object usage is no longer supported.`
        );
      }
      const data = dataOptions.call(publicThis, publicThis);
      if (isPromise(data)) {
        warn$1(
          `data() returned a Promise - note data() cannot be async; If you intend to perform data fetching before component renders, use async setup() + <Suspense>.`
        );
      }
      if (!isObject(data)) {
        warn$1(`data() should return an object.`);
      } else {
        instance.data = reactive(data);
        if (true) {
          for (const key in data) {
            checkDuplicateProperties("Data", key);
            if (!isReservedPrefix(key[0])) {
              Object.defineProperty(ctx, key, {
                configurable: true,
                enumerable: true,
                get: () => data[key],
                set: NOOP
              });
            }
          }
        }
      }
    }
    shouldCacheAccess = true;
    if (computedOptions) {
      for (const key in computedOptions) {
        const opt = computedOptions[key];
        const get2 = isFunction(opt) ? opt.bind(publicThis, publicThis) : isFunction(opt.get) ? opt.get.bind(publicThis, publicThis) : NOOP;
        if (get2 === NOOP) {
          warn$1(`Computed property "${key}" has no getter.`);
        }
        const set2 = !isFunction(opt) && isFunction(opt.set) ? opt.set.bind(publicThis) : true ? () => {
          warn$1(
            `Write operation failed: computed property "${key}" is readonly.`
          );
        } : NOOP;
        const c = computed2({
          get: get2,
          set: set2
        });
        Object.defineProperty(ctx, key, {
          enumerable: true,
          configurable: true,
          get: () => c.value,
          set: (v) => c.value = v
        });
        if (true) {
          checkDuplicateProperties("Computed", key);
        }
      }
    }
    if (watchOptions) {
      for (const key in watchOptions) {
        createWatcher(watchOptions[key], ctx, publicThis, key);
      }
    }
    if (provideOptions) {
      const provides = isFunction(provideOptions) ? provideOptions.call(publicThis) : provideOptions;
      Reflect.ownKeys(provides).forEach((key) => {
        provide(key, provides[key]);
      });
    }
    if (created) {
      callHook(created, instance, "c");
    }
    function registerLifecycleHook(register, hook) {
      if (isArray(hook)) {
        hook.forEach((_hook) => register(_hook.bind(publicThis)));
      } else if (hook) {
        register(hook.bind(publicThis));
      }
    }
    registerLifecycleHook(onBeforeMount, beforeMount);
    registerLifecycleHook(onMounted, mounted);
    registerLifecycleHook(onBeforeUpdate, beforeUpdate);
    registerLifecycleHook(onUpdated, updated);
    registerLifecycleHook(onActivated, activated);
    registerLifecycleHook(onDeactivated, deactivated);
    registerLifecycleHook(onErrorCaptured, errorCaptured);
    registerLifecycleHook(onRenderTracked, renderTracked);
    registerLifecycleHook(onRenderTriggered, renderTriggered);
    registerLifecycleHook(onBeforeUnmount, beforeUnmount);
    registerLifecycleHook(onUnmounted, unmounted);
    registerLifecycleHook(onServerPrefetch, serverPrefetch);
    if (isArray(expose)) {
      if (expose.length) {
        const exposed = instance.exposed || (instance.exposed = {});
        expose.forEach((key) => {
          Object.defineProperty(exposed, key, {
            get: () => publicThis[key],
            set: (val) => publicThis[key] = val,
            enumerable: true
          });
        });
      } else if (!instance.exposed) {
        instance.exposed = {};
      }
    }
    if (render2 && instance.render === NOOP) {
      instance.render = render2;
    }
    if (inheritAttrs != null) {
      instance.inheritAttrs = inheritAttrs;
    }
    if (components) instance.components = components;
    if (directives) instance.directives = directives;
    if (serverPrefetch) {
      markAsyncBoundary(instance);
    }
  }
  function resolveInjections(injectOptions, ctx, checkDuplicateProperties = NOOP) {
    if (isArray(injectOptions)) {
      injectOptions = normalizeInject(injectOptions);
    }
    for (const key in injectOptions) {
      const opt = injectOptions[key];
      let injected;
      if (isObject(opt)) {
        if ("default" in opt) {
          injected = inject(
            opt.from || key,
            opt.default,
            true
          );
        } else {
          injected = inject(opt.from || key);
        }
      } else {
        injected = inject(opt);
      }
      if (isRef2(injected)) {
        Object.defineProperty(ctx, key, {
          enumerable: true,
          configurable: true,
          get: () => injected.value,
          set: (v) => injected.value = v
        });
      } else {
        ctx[key] = injected;
      }
      if (true) {
        checkDuplicateProperties("Inject", key);
      }
    }
  }
  function callHook(hook, instance, type) {
    callWithAsyncErrorHandling(
      isArray(hook) ? hook.map((h2) => h2.bind(instance.proxy)) : hook.bind(instance.proxy),
      instance,
      type
    );
  }
  function createWatcher(raw, ctx, publicThis, key) {
    let getter = key.includes(".") ? createPathGetter(publicThis, key) : () => publicThis[key];
    if (isString(raw)) {
      const handler = ctx[raw];
      if (isFunction(handler)) {
        {
          watch2(getter, handler);
        }
      } else if (true) {
        warn$1(`Invalid watch handler specified by key "${raw}"`, handler);
      }
    } else if (isFunction(raw)) {
      {
        watch2(getter, raw.bind(publicThis));
      }
    } else if (isObject(raw)) {
      if (isArray(raw)) {
        raw.forEach((r) => createWatcher(r, ctx, publicThis, key));
      } else {
        const handler = isFunction(raw.handler) ? raw.handler.bind(publicThis) : ctx[raw.handler];
        if (isFunction(handler)) {
          watch2(getter, handler, raw);
        } else if (true) {
          warn$1(`Invalid watch handler specified by key "${raw.handler}"`, handler);
        }
      }
    } else if (true) {
      warn$1(`Invalid watch option: "${key}"`, raw);
    }
  }
  function resolveMergedOptions(instance) {
    const base = instance.type;
    const { mixins, extends: extendsOptions } = base;
    const {
      mixins: globalMixins,
      optionsCache: cache,
      config: { optionMergeStrategies }
    } = instance.appContext;
    const cached = cache.get(base);
    let resolved;
    if (cached) {
      resolved = cached;
    } else if (!globalMixins.length && !mixins && !extendsOptions) {
      {
        resolved = base;
      }
    } else {
      resolved = {};
      if (globalMixins.length) {
        globalMixins.forEach(
          (m) => mergeOptions(resolved, m, optionMergeStrategies, true)
        );
      }
      mergeOptions(resolved, base, optionMergeStrategies);
    }
    if (isObject(base)) {
      cache.set(base, resolved);
    }
    return resolved;
  }
  function mergeOptions(to, from, strats, asMixin = false) {
    const { mixins, extends: extendsOptions } = from;
    if (extendsOptions) {
      mergeOptions(to, extendsOptions, strats, true);
    }
    if (mixins) {
      mixins.forEach(
        (m) => mergeOptions(to, m, strats, true)
      );
    }
    for (const key in from) {
      if (asMixin && key === "expose") {
        warn$1(
          `"expose" option is ignored when declared in mixins or extends. It should only be declared in the base component itself.`
        );
      } else {
        const strat = internalOptionMergeStrats[key] || strats && strats[key];
        to[key] = strat ? strat(to[key], from[key]) : from[key];
      }
    }
    return to;
  }
  var internalOptionMergeStrats = {
    data: mergeDataFn,
    props: mergeEmitsOrPropsOptions,
    emits: mergeEmitsOrPropsOptions,
    // objects
    methods: mergeObjectOptions,
    computed: mergeObjectOptions,
    // lifecycle
    beforeCreate: mergeAsArray,
    created: mergeAsArray,
    beforeMount: mergeAsArray,
    mounted: mergeAsArray,
    beforeUpdate: mergeAsArray,
    updated: mergeAsArray,
    beforeDestroy: mergeAsArray,
    beforeUnmount: mergeAsArray,
    destroyed: mergeAsArray,
    unmounted: mergeAsArray,
    activated: mergeAsArray,
    deactivated: mergeAsArray,
    errorCaptured: mergeAsArray,
    serverPrefetch: mergeAsArray,
    // assets
    components: mergeObjectOptions,
    directives: mergeObjectOptions,
    // watch
    watch: mergeWatchOptions,
    // provide / inject
    provide: mergeDataFn,
    inject: mergeInject
  };
  function mergeDataFn(to, from) {
    if (!from) {
      return to;
    }
    if (!to) {
      return from;
    }
    return function mergedDataFn() {
      return extend(
        isFunction(to) ? to.call(this, this) : to,
        isFunction(from) ? from.call(this, this) : from
      );
    };
  }
  function mergeInject(to, from) {
    return mergeObjectOptions(normalizeInject(to), normalizeInject(from));
  }
  function normalizeInject(raw) {
    if (isArray(raw)) {
      const res = {};
      for (let i = 0; i < raw.length; i++) {
        res[raw[i]] = raw[i];
      }
      return res;
    }
    return raw;
  }
  function mergeAsArray(to, from) {
    return to ? [...new Set([].concat(to, from))] : from;
  }
  function mergeObjectOptions(to, from) {
    return to ? extend(/* @__PURE__ */ Object.create(null), to, from) : from;
  }
  function mergeEmitsOrPropsOptions(to, from) {
    if (to) {
      if (isArray(to) && isArray(from)) {
        return [.../* @__PURE__ */ new Set([...to, ...from])];
      }
      return extend(
        /* @__PURE__ */ Object.create(null),
        normalizePropsOrEmits(to),
        normalizePropsOrEmits(from != null ? from : {})
      );
    } else {
      return from;
    }
  }
  function mergeWatchOptions(to, from) {
    if (!to) return from;
    if (!from) return to;
    const merged = extend(/* @__PURE__ */ Object.create(null), to);
    for (const key in from) {
      merged[key] = mergeAsArray(to[key], from[key]);
    }
    return merged;
  }
  function createAppContext() {
    return {
      app: null,
      config: {
        isNativeTag: NO,
        performance: false,
        globalProperties: {},
        optionMergeStrategies: {},
        errorHandler: void 0,
        warnHandler: void 0,
        compilerOptions: {}
      },
      mixins: [],
      components: {},
      directives: {},
      provides: /* @__PURE__ */ Object.create(null),
      optionsCache: /* @__PURE__ */ new WeakMap(),
      propsCache: /* @__PURE__ */ new WeakMap(),
      emitsCache: /* @__PURE__ */ new WeakMap()
    };
  }
  var uid$1 = 0;
  function createAppAPI(render2, hydrate) {
    return function createApp2(rootComponent, rootProps = null) {
      if (!isFunction(rootComponent)) {
        rootComponent = extend({}, rootComponent);
      }
      if (rootProps != null && !isObject(rootProps)) {
        warn$1(`root props passed to app.mount() must be an object.`);
        rootProps = null;
      }
      const context = createAppContext();
      const installedPlugins = /* @__PURE__ */ new WeakSet();
      const pluginCleanupFns = [];
      let isMounted = false;
      const app = context.app = {
        _uid: uid$1++,
        _component: rootComponent,
        _props: rootProps,
        _container: null,
        _context: context,
        _instance: null,
        version,
        get config() {
          return context.config;
        },
        set config(v) {
          if (true) {
            warn$1(
              `app.config cannot be replaced. Modify individual options instead.`
            );
          }
        },
        use(plugin, ...options) {
          if (installedPlugins.has(plugin)) {
            warn$1(`Plugin has already been applied to target app.`);
          } else if (plugin && isFunction(plugin.install)) {
            installedPlugins.add(plugin);
            plugin.install(app, ...options);
          } else if (isFunction(plugin)) {
            installedPlugins.add(plugin);
            plugin(app, ...options);
          } else if (true) {
            warn$1(
              `A plugin must either be a function or an object with an "install" function.`
            );
          }
          return app;
        },
        mixin(mixin) {
          if (true) {
            if (!context.mixins.includes(mixin)) {
              context.mixins.push(mixin);
            } else if (true) {
              warn$1(
                "Mixin has already been applied to target app" + (mixin.name ? `: ${mixin.name}` : "")
              );
            }
          } else if (true) {
            warn$1("Mixins are only available in builds supporting Options API");
          }
          return app;
        },
        component(name, component) {
          if (true) {
            validateComponentName(name, context.config);
          }
          if (!component) {
            return context.components[name];
          }
          if (context.components[name]) {
            warn$1(`Component "${name}" has already been registered in target app.`);
          }
          context.components[name] = component;
          return app;
        },
        directive(name, directive) {
          if (true) {
            validateDirectiveName(name);
          }
          if (!directive) {
            return context.directives[name];
          }
          if (context.directives[name]) {
            warn$1(`Directive "${name}" has already been registered in target app.`);
          }
          context.directives[name] = directive;
          return app;
        },
        mount(rootContainer, isHydrate, namespace2) {
          if (!isMounted) {
            if (rootContainer.__vue_app__) {
              warn$1(
                `There is already an app instance mounted on the host container.
 If you want to mount another app on the same host container, you need to unmount the previous app by calling \`app.unmount()\` first.`
              );
            }
            const vnode = app._ceVNode || createVNode(rootComponent, rootProps);
            vnode.appContext = context;
            if (namespace2 === true) {
              namespace2 = "svg";
            } else if (namespace2 === false) {
              namespace2 = void 0;
            }
            if (true) {
              context.reload = () => {
                const cloned = cloneVNode(vnode);
                cloned.el = null;
                render2(cloned, rootContainer, namespace2);
              };
            }
            if (isHydrate && hydrate) {
              hydrate(vnode, rootContainer);
            } else {
              render2(vnode, rootContainer, namespace2);
            }
            isMounted = true;
            app._container = rootContainer;
            rootContainer.__vue_app__ = app;
            if (true) {
              app._instance = vnode.component;
              devtoolsInitApp(app, version);
            }
            return getComponentPublicInstance(vnode.component);
          } else if (true) {
            warn$1(
              `App has already been mounted.
If you want to remount the same app, move your app creation logic into a factory function and create fresh app instances for each mount - e.g. \`const createMyApp = () => createApp(App)\``
            );
          }
        },
        onUnmount(cleanupFn) {
          if (typeof cleanupFn !== "function") {
            warn$1(
              `Expected function as first argument to app.onUnmount(), but got ${typeof cleanupFn}`
            );
          }
          pluginCleanupFns.push(cleanupFn);
        },
        unmount() {
          if (isMounted) {
            callWithAsyncErrorHandling(
              pluginCleanupFns,
              app._instance,
              16
            );
            render2(null, app._container);
            if (true) {
              app._instance = null;
              devtoolsUnmountApp(app);
            }
            delete app._container.__vue_app__;
          } else if (true) {
            warn$1(`Cannot unmount an app that is not mounted.`);
          }
        },
        provide(key, value) {
          if (key in context.provides) {
            if (hasOwn(context.provides, key)) {
              warn$1(
                `App already provides property with key "${String(key)}". It will be overwritten with the new value.`
              );
            } else {
              warn$1(
                `App already provides property with key "${String(key)}" inherited from its parent element. It will be overwritten with the new value.`
              );
            }
          }
          context.provides[key] = value;
          return app;
        },
        runWithContext(fn) {
          const lastApp = currentApp;
          currentApp = app;
          try {
            return fn();
          } finally {
            currentApp = lastApp;
          }
        }
      };
      return app;
    };
  }
  var currentApp = null;
  var getModelModifiers = (props, modelName) => {
    return modelName === "modelValue" || modelName === "model-value" ? props.modelModifiers : props[`${modelName}Modifiers`] || props[`${camelize(modelName)}Modifiers`] || props[`${hyphenate(modelName)}Modifiers`];
  };
  function emit(instance, event, ...rawArgs) {
    if (instance.isUnmounted) return;
    const props = instance.vnode.props || EMPTY_OBJ;
    if (true) {
      const {
        emitsOptions,
        propsOptions: [propsOptions]
      } = instance;
      if (emitsOptions) {
        if (!(event in emitsOptions) && true) {
          if (!propsOptions || !(toHandlerKey(camelize(event)) in propsOptions)) {
            warn$1(
              `Component emitted event "${event}" but it is neither declared in the emits option nor as an "${toHandlerKey(camelize(event))}" prop.`
            );
          }
        } else {
          const validator = emitsOptions[event];
          if (isFunction(validator)) {
            const isValid = validator(...rawArgs);
            if (!isValid) {
              warn$1(
                `Invalid event arguments: event validation failed for event "${event}".`
              );
            }
          }
        }
      }
    }
    let args = rawArgs;
    const isModelListener2 = event.startsWith("update:");
    const modifiers = isModelListener2 && getModelModifiers(props, event.slice(7));
    if (modifiers) {
      if (modifiers.trim) {
        args = rawArgs.map((a) => isString(a) ? a.trim() : a);
      }
      if (modifiers.number) {
        args = args.map(looseToNumber);
      }
    }
    if (true) {
      devtoolsComponentEmit(instance, event, args);
    }
    if (true) {
      const lowerCaseEvent = event.toLowerCase();
      if (lowerCaseEvent !== event && props[toHandlerKey(lowerCaseEvent)]) {
        warn$1(
          `Event "${lowerCaseEvent}" is emitted in component ${formatComponentName(
            instance,
            instance.type
          )} but the handler is registered for "${event}". Note that HTML attributes are case-insensitive and you cannot use v-on to listen to camelCase events when using in-DOM templates. You should probably use "${hyphenate(
            event
          )}" instead of "${event}".`
        );
      }
    }
    let handlerName;
    let handler = props[handlerName = toHandlerKey(event)] || // also try camelCase event handler (#2249)
    props[handlerName = toHandlerKey(camelize(event))];
    if (!handler && isModelListener2) {
      handler = props[handlerName = toHandlerKey(hyphenate(event))];
    }
    if (handler) {
      callWithAsyncErrorHandling(
        handler,
        instance,
        6,
        args
      );
    }
    const onceHandler = props[handlerName + `Once`];
    if (onceHandler) {
      if (!instance.emitted) {
        instance.emitted = {};
      } else if (instance.emitted[handlerName]) {
        return;
      }
      instance.emitted[handlerName] = true;
      callWithAsyncErrorHandling(
        onceHandler,
        instance,
        6,
        args
      );
    }
  }
  var mixinEmitsCache = /* @__PURE__ */ new WeakMap();
  function normalizeEmitsOptions(comp, appContext, asMixin = false) {
    const cache = asMixin ? mixinEmitsCache : appContext.emitsCache;
    const cached = cache.get(comp);
    if (cached !== void 0) {
      return cached;
    }
    const raw = comp.emits;
    let normalized = {};
    let hasExtends = false;
    if (!isFunction(comp)) {
      const extendEmits = (raw2) => {
        const normalizedFromExtend = normalizeEmitsOptions(raw2, appContext, true);
        if (normalizedFromExtend) {
          hasExtends = true;
          extend(normalized, normalizedFromExtend);
        }
      };
      if (!asMixin && appContext.mixins.length) {
        appContext.mixins.forEach(extendEmits);
      }
      if (comp.extends) {
        extendEmits(comp.extends);
      }
      if (comp.mixins) {
        comp.mixins.forEach(extendEmits);
      }
    }
    if (!raw && !hasExtends) {
      if (isObject(comp)) {
        cache.set(comp, null);
      }
      return null;
    }
    if (isArray(raw)) {
      raw.forEach((key) => normalized[key] = null);
    } else {
      extend(normalized, raw);
    }
    if (isObject(comp)) {
      cache.set(comp, normalized);
    }
    return normalized;
  }
  function isEmitListener(options, key) {
    if (!options || !isOn(key)) {
      return false;
    }
    key = key.slice(2);
    key = key === "Once" ? key : key.replace(/Once$/, "");
    return hasOwn(options, key[0].toLowerCase() + key.slice(1)) || hasOwn(options, hyphenate(key)) || hasOwn(options, key);
  }
  var accessedAttrs = false;
  function markAttrsAccessed() {
    accessedAttrs = true;
  }
  function renderComponentRoot(instance) {
    const {
      type: Component,
      vnode,
      proxy,
      withProxy,
      propsOptions: [propsOptions],
      slots,
      attrs,
      emit: emit2,
      render: render2,
      renderCache,
      props,
      data,
      setupState,
      ctx,
      inheritAttrs
    } = instance;
    const prev = setCurrentRenderingInstance(instance);
    let result;
    let fallthroughAttrs;
    if (true) {
      accessedAttrs = false;
    }
    try {
      if (vnode.shapeFlag & 4) {
        const proxyToUse = withProxy || proxy;
        const thisProxy = setupState.__isScriptSetup ? new Proxy(proxyToUse, {
          get(target, key, receiver) {
            warn$1(
              `Property '${String(
                key
              )}' was accessed via 'this'. Avoid using 'this' in templates.`
            );
            return Reflect.get(target, key, receiver);
          }
        }) : proxyToUse;
        result = normalizeVNode(
          render2.call(
            thisProxy,
            proxyToUse,
            renderCache,
            true ? shallowReadonly(props) : props,
            setupState,
            data,
            ctx
          )
        );
        fallthroughAttrs = attrs;
      } else {
        const render22 = Component;
        if (attrs === props) {
          markAttrsAccessed();
        }
        result = normalizeVNode(
          render22.length > 1 ? render22(
            true ? shallowReadonly(props) : props,
            true ? {
              get attrs() {
                markAttrsAccessed();
                return shallowReadonly(attrs);
              },
              slots,
              emit: emit2
            } : { attrs, slots, emit: emit2 }
          ) : render22(
            true ? shallowReadonly(props) : props,
            null
          )
        );
        fallthroughAttrs = Component.props ? attrs : getFunctionalFallthrough(attrs);
      }
    } catch (err) {
      blockStack.length = 0;
      handleError(err, instance, 1);
      result = createVNode(Comment);
    }
    let root2 = result;
    let setRoot = void 0;
    if (result.patchFlag > 0 && result.patchFlag & 2048) {
      [root2, setRoot] = getChildRoot(result);
    }
    if (fallthroughAttrs && inheritAttrs !== false) {
      const keys = Object.keys(fallthroughAttrs);
      const { shapeFlag } = root2;
      if (keys.length) {
        if (shapeFlag & (1 | 6)) {
          if (propsOptions && keys.some(isModelListener)) {
            fallthroughAttrs = filterModelListeners(
              fallthroughAttrs,
              propsOptions
            );
          }
          root2 = cloneVNode(root2, fallthroughAttrs, false, true);
        } else if (!accessedAttrs && root2.type !== Comment) {
          const allAttrs = Object.keys(attrs);
          const eventAttrs = [];
          const extraAttrs = [];
          for (let i = 0, l = allAttrs.length; i < l; i++) {
            const key = allAttrs[i];
            if (isOn(key)) {
              if (!isModelListener(key)) {
                eventAttrs.push(key[2].toLowerCase() + key.slice(3));
              }
            } else {
              extraAttrs.push(key);
            }
          }
          if (extraAttrs.length) {
            warn$1(
              `Extraneous non-props attributes (${extraAttrs.join(", ")}) were passed to component but could not be automatically inherited because component renders fragment or text or teleport root nodes.`
            );
          }
          if (eventAttrs.length) {
            warn$1(
              `Extraneous non-emits event listeners (${eventAttrs.join(", ")}) were passed to component but could not be automatically inherited because component renders fragment or text root nodes. If the listener is intended to be a component custom event listener only, declare it using the "emits" option.`
            );
          }
        }
      }
    }
    if (vnode.dirs) {
      if (!isElementRoot(root2)) {
        warn$1(
          `Runtime directive used on component with non-element root node. The directives will not function as intended.`
        );
      }
      root2 = cloneVNode(root2, null, false, true);
      root2.dirs = root2.dirs ? root2.dirs.concat(vnode.dirs) : vnode.dirs;
    }
    if (vnode.transition) {
      const child = isTeleport(root2.type) ? getInnerChild$1(root2) || root2 : root2;
      if (!isElementRoot(child)) {
        warn$1(
          `Component inside <Transition> renders non-element root node that cannot be animated.`
        );
      }
      setTransitionHooks(child, vnode.transition);
    }
    if (setRoot) {
      setRoot(root2);
    } else {
      result = root2;
    }
    setCurrentRenderingInstance(prev);
    return result;
  }
  var getChildRoot = (vnode) => {
    const rawChildren = vnode.children;
    const dynamicChildren = vnode.dynamicChildren;
    const childRoot = filterSingleRoot(rawChildren, false);
    if (!childRoot) {
      return [vnode, void 0];
    } else if (childRoot.patchFlag > 0 && childRoot.patchFlag & 2048) {
      return getChildRoot(childRoot);
    }
    const index = rawChildren.indexOf(childRoot);
    const dynamicIndex = dynamicChildren ? dynamicChildren.indexOf(childRoot) : -1;
    const setRoot = (updatedRoot) => {
      rawChildren[index] = updatedRoot;
      if (dynamicChildren) {
        if (dynamicIndex > -1) {
          dynamicChildren[dynamicIndex] = updatedRoot;
        } else if (updatedRoot.patchFlag > 0) {
          vnode.dynamicChildren = [...dynamicChildren, updatedRoot];
        }
      }
    };
    return [normalizeVNode(childRoot), setRoot];
  };
  function filterSingleRoot(children2, recurse = true) {
    let singleRoot;
    for (let i = 0; i < children2.length; i++) {
      const child = children2[i];
      if (isVNode(child)) {
        if (child.type !== Comment || child.children === "v-if") {
          if (singleRoot) {
            return;
          } else {
            singleRoot = child;
            if (recurse && singleRoot.patchFlag > 0 && singleRoot.patchFlag & 2048) {
              return filterSingleRoot(singleRoot.children);
            }
          }
        }
      } else {
        return;
      }
    }
    return singleRoot;
  }
  var getFunctionalFallthrough = (attrs) => {
    let res;
    for (const key in attrs) {
      if (key === "class" || key === "style" || isOn(key)) {
        (res || (res = {}))[key] = attrs[key];
      }
    }
    return res;
  };
  var filterModelListeners = (attrs, props) => {
    const res = {};
    for (const key in attrs) {
      if (!isModelListener(key) || !(key.slice(9) in props)) {
        res[key] = attrs[key];
      }
    }
    return res;
  };
  var isElementRoot = (vnode) => {
    return vnode.shapeFlag & (6 | 1) || vnode.type === Comment;
  };
  function shouldUpdateComponent(prevVNode, nextVNode, optimized) {
    const { props: prevProps, children: prevChildren, component } = prevVNode;
    const { props: nextProps, children: nextChildren, patchFlag } = nextVNode;
    const emits = component.emitsOptions;
    if ((prevChildren || nextChildren) && isHmrUpdating) {
      return true;
    }
    if (nextVNode.dirs || nextVNode.transition) {
      return true;
    }
    if (optimized && patchFlag >= 0) {
      if (patchFlag & 1024) {
        return true;
      }
      if (patchFlag & 16) {
        if (!prevProps) {
          return !!nextProps;
        }
        return hasPropsChanged(prevProps, nextProps, emits);
      } else if (patchFlag & 8) {
        const dynamicProps = nextVNode.dynamicProps;
        for (let i = 0; i < dynamicProps.length; i++) {
          const key = dynamicProps[i];
          if (hasPropValueChanged(nextProps, prevProps, key) && !isEmitListener(emits, key)) {
            return true;
          }
        }
      }
    } else {
      if (prevChildren || nextChildren) {
        if (!nextChildren || !nextChildren.$stable) {
          return true;
        }
      }
      if (prevProps === nextProps) {
        return false;
      }
      if (!prevProps) {
        return !!nextProps;
      }
      if (!nextProps) {
        return true;
      }
      return hasPropsChanged(prevProps, nextProps, emits);
    }
    return false;
  }
  function hasPropsChanged(prevProps, nextProps, emitsOptions) {
    const nextKeys = Object.keys(nextProps);
    if (nextKeys.length !== Object.keys(prevProps).length) {
      return true;
    }
    for (let i = 0; i < nextKeys.length; i++) {
      const key = nextKeys[i];
      if (!hasOwn(prevProps, key) || hasPropValueChanged(nextProps, prevProps, key) && !isEmitListener(emitsOptions, key)) {
        return true;
      }
    }
    return false;
  }
  function hasPropValueChanged(nextProps, prevProps, key) {
    const nextProp = nextProps[key];
    const prevProp = prevProps[key];
    if (key === "style" && isObject(nextProp) && isObject(prevProp)) {
      return !looseEqual(nextProp, prevProp);
    }
    return nextProp !== prevProp;
  }
  function updateHOCHostEl({ vnode, parent, suspense }, el) {
    while (parent) {
      const root2 = parent.subTree;
      if (root2.suspense && root2.suspense.activeBranch === vnode) {
        root2.suspense.vnode.el = root2.el = el;
        vnode = root2;
      }
      if (root2 === vnode) {
        (vnode = parent.vnode).el = el;
        parent = parent.parent;
      } else {
        break;
      }
    }
    if (suspense && suspense.activeBranch === vnode) {
      suspense.vnode.el = el;
    }
  }
  var internalObjectProto = {};
  var createInternalObject = () => Object.create(internalObjectProto);
  var isInternalObject = (obj) => Object.getPrototypeOf(obj) === internalObjectProto;
  function initProps(instance, rawProps, isStateful, isSSR = false) {
    const props = {};
    const attrs = createInternalObject();
    instance.propsDefaults = /* @__PURE__ */ Object.create(null);
    setFullProps(instance, rawProps, props, attrs);
    for (const key in instance.propsOptions[0]) {
      if (!(key in props)) {
        props[key] = void 0;
      }
    }
    if (true) {
      validateProps(rawProps || {}, props, instance);
    }
    if (isStateful) {
      instance.props = isSSR ? props : shallowReactive(props);
    } else {
      if (!instance.type.props) {
        instance.props = attrs;
      } else {
        instance.props = props;
      }
    }
    instance.attrs = attrs;
  }
  function isInHmrContext(instance) {
    while (instance) {
      if (instance.type.__hmrId) return true;
      instance = instance.parent;
    }
  }
  function updateProps(instance, rawProps, rawPrevProps, optimized) {
    const {
      props,
      attrs,
      vnode: { patchFlag }
    } = instance;
    const rawCurrentProps = toRaw(props);
    const [options] = instance.propsOptions;
    let hasAttrsChanged = false;
    if (
      // always force full diff in dev
      // - #1942 if hmr is enabled with sfc component
      // - vite#872 non-sfc component used by sfc component
      !isInHmrContext(instance) && (optimized || patchFlag > 0) && !(patchFlag & 16)
    ) {
      if (patchFlag & 8) {
        const propsToUpdate = instance.vnode.dynamicProps;
        for (let i = 0; i < propsToUpdate.length; i++) {
          let key = propsToUpdate[i];
          if (isEmitListener(instance.emitsOptions, key)) {
            continue;
          }
          const value = rawProps[key];
          if (options) {
            if (hasOwn(attrs, key)) {
              if (value !== attrs[key]) {
                attrs[key] = value;
                hasAttrsChanged = true;
              }
            } else {
              const camelizedKey = camelize(key);
              props[camelizedKey] = resolvePropValue(
                options,
                rawCurrentProps,
                camelizedKey,
                value,
                instance,
                false
              );
            }
          } else {
            if (value !== attrs[key]) {
              attrs[key] = value;
              hasAttrsChanged = true;
            }
          }
        }
      }
    } else {
      if (setFullProps(instance, rawProps, props, attrs)) {
        hasAttrsChanged = true;
      }
      let kebabKey;
      for (const key in rawCurrentProps) {
        if (!rawProps || // for camelCase
        !hasOwn(rawProps, key) && // it's possible the original props was passed in as kebab-case
        // and converted to camelCase (#955)
        ((kebabKey = hyphenate(key)) === key || !hasOwn(rawProps, kebabKey))) {
          if (options) {
            if (rawPrevProps && // for camelCase
            (rawPrevProps[key] !== void 0 || // for kebab-case
            rawPrevProps[kebabKey] !== void 0)) {
              props[key] = resolvePropValue(
                options,
                rawCurrentProps,
                key,
                void 0,
                instance,
                true
              );
            }
          } else {
            delete props[key];
          }
        }
      }
      if (attrs !== rawCurrentProps) {
        for (const key in attrs) {
          if (!rawProps || !hasOwn(rawProps, key) && true) {
            delete attrs[key];
            hasAttrsChanged = true;
          }
        }
      }
    }
    if (hasAttrsChanged) {
      trigger(instance.attrs, "set", "");
    }
    if (true) {
      validateProps(rawProps || {}, props, instance);
    }
  }
  function setFullProps(instance, rawProps, props, attrs) {
    const [options, needCastKeys] = instance.propsOptions;
    let hasAttrsChanged = false;
    let rawCastValues;
    if (rawProps) {
      for (let key in rawProps) {
        if (isReservedProp(key)) {
          continue;
        }
        const value = rawProps[key];
        let camelKey;
        if (options && hasOwn(options, camelKey = camelize(key))) {
          if (!needCastKeys || !needCastKeys.includes(camelKey)) {
            props[camelKey] = value;
          } else {
            (rawCastValues || (rawCastValues = {}))[camelKey] = value;
          }
        } else if (!isEmitListener(instance.emitsOptions, key)) {
          if (!(key in attrs) || value !== attrs[key]) {
            attrs[key] = value;
            hasAttrsChanged = true;
          }
        }
      }
    }
    if (needCastKeys) {
      const rawCurrentProps = toRaw(props);
      const castValues = rawCastValues || EMPTY_OBJ;
      for (let i = 0; i < needCastKeys.length; i++) {
        const key = needCastKeys[i];
        props[key] = resolvePropValue(
          options,
          rawCurrentProps,
          key,
          castValues[key],
          instance,
          !hasOwn(castValues, key)
        );
      }
    }
    return hasAttrsChanged;
  }
  function resolvePropValue(options, props, key, value, instance, isAbsent) {
    const opt = options[key];
    if (opt != null) {
      const hasDefault = hasOwn(opt, "default");
      if (hasDefault && value === void 0) {
        const defaultValue = opt.default;
        if (opt.type !== Function && !opt.skipFactory && isFunction(defaultValue)) {
          const { propsDefaults } = instance;
          if (key in propsDefaults) {
            value = propsDefaults[key];
          } else {
            const reset = setCurrentInstance(instance);
            value = propsDefaults[key] = defaultValue.call(
              null,
              props
            );
            reset();
          }
        } else {
          value = defaultValue;
        }
        if (instance.ce) {
          instance.ce._setProp(key, value);
        }
      }
      if (opt[
        0
        /* shouldCast */
      ]) {
        if (isAbsent && !hasDefault) {
          value = false;
        } else if (opt[
          1
          /* shouldCastTrue */
        ] && (value === "" || value === hyphenate(key))) {
          value = true;
        }
      }
    }
    return value;
  }
  var mixinPropsCache = /* @__PURE__ */ new WeakMap();
  function normalizePropsOptions(comp, appContext, asMixin = false) {
    const cache = asMixin ? mixinPropsCache : appContext.propsCache;
    const cached = cache.get(comp);
    if (cached) {
      return cached;
    }
    const raw = comp.props;
    const normalized = {};
    const needCastKeys = [];
    let hasExtends = false;
    if (!isFunction(comp)) {
      const extendProps = (raw2) => {
        hasExtends = true;
        const [props, keys] = normalizePropsOptions(raw2, appContext, true);
        extend(normalized, props);
        if (keys) needCastKeys.push(...keys);
      };
      if (!asMixin && appContext.mixins.length) {
        appContext.mixins.forEach(extendProps);
      }
      if (comp.extends) {
        extendProps(comp.extends);
      }
      if (comp.mixins) {
        comp.mixins.forEach(extendProps);
      }
    }
    if (!raw && !hasExtends) {
      if (isObject(comp)) {
        cache.set(comp, EMPTY_ARR);
      }
      return EMPTY_ARR;
    }
    if (isArray(raw)) {
      for (let i = 0; i < raw.length; i++) {
        if (!isString(raw[i])) {
          warn$1(`props must be strings when using array syntax.`, raw[i]);
        }
        const normalizedKey = camelize(raw[i]);
        if (validatePropName(normalizedKey)) {
          normalized[normalizedKey] = EMPTY_OBJ;
        }
      }
    } else if (raw) {
      if (!isObject(raw)) {
        warn$1(`invalid props options`, raw);
      }
      for (const key in raw) {
        const normalizedKey = camelize(key);
        if (validatePropName(normalizedKey)) {
          const opt = raw[key];
          const prop = normalized[normalizedKey] = isArray(opt) || isFunction(opt) ? { type: opt } : extend({}, opt);
          const propType = prop.type;
          let shouldCast = false;
          let shouldCastTrue = true;
          if (isArray(propType)) {
            for (let index = 0; index < propType.length; ++index) {
              const type = propType[index];
              const typeName = isFunction(type) && type.name;
              if (typeName === "Boolean") {
                shouldCast = true;
                break;
              } else if (typeName === "String") {
                shouldCastTrue = false;
              }
            }
          } else {
            shouldCast = isFunction(propType) && propType.name === "Boolean";
          }
          prop[
            0
            /* shouldCast */
          ] = shouldCast;
          prop[
            1
            /* shouldCastTrue */
          ] = shouldCastTrue;
          if (shouldCast || hasOwn(prop, "default")) {
            needCastKeys.push(normalizedKey);
          }
        }
      }
    }
    const res = [normalized, needCastKeys];
    if (isObject(comp)) {
      cache.set(comp, res);
    }
    return res;
  }
  function validatePropName(key) {
    if (key[0] !== "$" && !isReservedProp(key)) {
      return true;
    } else if (true) {
      warn$1(`Invalid prop name: "${key}" is a reserved property.`);
    }
    return false;
  }
  function getType(ctor) {
    if (ctor === null) {
      return "null";
    }
    if (typeof ctor === "function") {
      return ctor.name || "";
    } else if (typeof ctor === "object") {
      const name = ctor.constructor && ctor.constructor.name;
      return name || "";
    }
    return "";
  }
  function validateProps(rawProps, props, instance) {
    const resolvedValues = toRaw(props);
    const options = instance.propsOptions[0];
    const camelizePropsKey = Object.keys(rawProps).map((key) => camelize(key));
    for (const key in options) {
      let opt = options[key];
      if (opt == null) continue;
      validateProp(
        key,
        resolvedValues[key],
        opt,
        true ? shallowReadonly(resolvedValues) : resolvedValues,
        !camelizePropsKey.includes(key)
      );
    }
  }
  function validateProp(name, value, prop, props, isAbsent) {
    const { type, required, validator, skipCheck } = prop;
    if (required && isAbsent) {
      warn$1('Missing required prop: "' + name + '"');
      return;
    }
    if (value == null && !required) {
      return;
    }
    if (type != null && type !== true && !skipCheck) {
      let isValid = false;
      const types = isArray(type) ? type : [type];
      const expectedTypes = [];
      for (let i = 0; i < types.length && !isValid; i++) {
        const { valid, expectedType } = assertType(value, types[i]);
        expectedTypes.push(expectedType || "");
        isValid = valid;
      }
      if (!isValid) {
        warn$1(getInvalidTypeMessage(name, value, expectedTypes));
        return;
      }
    }
    if (validator && !validator(value, props)) {
      warn$1('Invalid prop: custom validator check failed for prop "' + name + '".');
    }
  }
  var isSimpleType = /* @__PURE__ */ makeMap(
    "String,Number,Boolean,Function,Symbol,BigInt"
  );
  function assertType(value, type) {
    let valid;
    const expectedType = getType(type);
    if (expectedType === "null") {
      valid = value === null;
    } else if (isSimpleType(expectedType)) {
      const t = typeof value;
      valid = t === expectedType.toLowerCase();
      if (!valid && t === "object") {
        valid = value instanceof type;
      }
    } else if (expectedType === "Object") {
      valid = isObject(value);
    } else if (expectedType === "Array") {
      valid = isArray(value);
    } else {
      valid = value instanceof type;
    }
    return {
      valid,
      expectedType
    };
  }
  function getInvalidTypeMessage(name, value, expectedTypes) {
    if (expectedTypes.length === 0) {
      return `Prop type [] for prop "${name}" won't match anything. Did you mean to use type Array instead?`;
    }
    let message = `Invalid prop: type check failed for prop "${name}". Expected ${expectedTypes.map(capitalize).join(" | ")}`;
    const expectedType = expectedTypes[0];
    const receivedType = toRawType(value);
    const expectedValue = styleValue(value, expectedType);
    const receivedValue = styleValue(value, receivedType);
    if (expectedTypes.length === 1 && isExplicable(expectedType) && isCoercible(expectedType, receivedType)) {
      message += ` with value ${expectedValue}`;
    }
    message += `, got ${receivedType} `;
    if (isExplicable(receivedType)) {
      message += `with value ${receivedValue}.`;
    }
    return message;
  }
  function styleValue(value, type) {
    if (isSymbol(value)) {
      return value.toString();
    } else if (type === "String") {
      return `"${value}"`;
    } else if (type === "Number") {
      return `${Number(value)}`;
    } else {
      return `${value}`;
    }
  }
  function isExplicable(type) {
    const explicitTypes = ["string", "number", "boolean"];
    return explicitTypes.some((elem) => type.toLowerCase() === elem);
  }
  function isCoercible(...args) {
    return args.every((elem) => {
      const value = elem.toLowerCase();
      return value !== "boolean" && value !== "symbol";
    });
  }
  var isInternalKey = (key) => key === "_" || key === "_ctx" || key === "$stable";
  var normalizeSlotValue = (value) => isArray(value) ? value.map(normalizeVNode) : [normalizeVNode(value)];
  var normalizeSlot = (key, rawSlot, ctx) => {
    if (rawSlot._n) {
      return rawSlot;
    }
    const normalized = withCtx((...args) => {
      if (currentInstance && !(ctx === null && currentRenderingInstance) && !(ctx && ctx.root !== currentInstance.root)) {
        warn$1(
          `Slot "${key}" invoked outside of the render function: this will not track dependencies used in the slot. Invoke the slot function inside the render function instead.`
        );
      }
      return normalizeSlotValue(rawSlot(...args));
    }, ctx);
    normalized._c = false;
    return normalized;
  };
  var normalizeObjectSlots = (rawSlots, slots, instance) => {
    const ctx = rawSlots._ctx;
    for (const key in rawSlots) {
      if (isInternalKey(key)) continue;
      const value = rawSlots[key];
      if (isFunction(value)) {
        slots[key] = normalizeSlot(key, value, ctx);
      } else if (value != null) {
        if (true) {
          warn$1(
            `Non-function value encountered for slot "${key}". Prefer function slots for better performance.`
          );
        }
        const normalized = normalizeSlotValue(value);
        slots[key] = () => normalized;
      }
    }
  };
  var normalizeVNodeSlots = (instance, children2) => {
    if (!isKeepAlive(instance.vnode) && true) {
      warn$1(
        `Non-function value encountered for default slot. Prefer function slots for better performance.`
      );
    }
    const normalized = normalizeSlotValue(children2);
    instance.slots.default = () => normalized;
  };
  var assignSlots = (slots, children2, optimized) => {
    for (const key in children2) {
      if (optimized || !isInternalKey(key)) {
        slots[key] = children2[key];
      }
    }
  };
  var initSlots = (instance, children2, optimized) => {
    const slots = instance.slots = createInternalObject();
    if (instance.vnode.shapeFlag & 32) {
      const type = children2._;
      if (type) {
        assignSlots(slots, children2, optimized);
        if (optimized) {
          def(slots, "_", type, true);
        }
      } else {
        normalizeObjectSlots(children2, slots);
      }
    } else if (children2) {
      normalizeVNodeSlots(instance, children2);
    }
  };
  var updateSlots = (instance, children2, optimized) => {
    const { vnode, slots } = instance;
    let needDeletionCheck = true;
    let deletionComparisonTarget = EMPTY_OBJ;
    if (vnode.shapeFlag & 32) {
      const type = children2._;
      if (type) {
        if (isHmrUpdating) {
          assignSlots(slots, children2, optimized);
          trigger(instance, "set", "$slots");
        } else if (optimized && type === 1) {
          needDeletionCheck = false;
        } else {
          assignSlots(slots, children2, optimized);
        }
      } else {
        needDeletionCheck = !children2.$stable;
        normalizeObjectSlots(children2, slots);
      }
      deletionComparisonTarget = children2;
    } else if (children2) {
      normalizeVNodeSlots(instance, children2);
      deletionComparisonTarget = { default: 1 };
    }
    if (needDeletionCheck) {
      for (const key in slots) {
        if (!isInternalKey(key) && deletionComparisonTarget[key] == null) {
          delete slots[key];
        }
      }
    }
  };
  var supported;
  var perf;
  function startMeasure(instance, type) {
    if (instance.appContext.config.performance && isSupported()) {
      perf.mark(`vue-${type}-${instance.uid}`);
    }
    if (true) {
      devtoolsPerfStart(instance, type, isSupported() ? perf.now() : Date.now());
    }
  }
  function endMeasure(instance, type) {
    if (instance.appContext.config.performance && isSupported()) {
      const startTag = `vue-${type}-${instance.uid}`;
      const endTag = startTag + `:end`;
      const measureName = `<${formatComponentName(instance, instance.type)}> ${type}`;
      perf.mark(endTag);
      perf.measure(measureName, startTag, endTag);
      perf.clearMeasures(measureName);
      perf.clearMarks(startTag);
      perf.clearMarks(endTag);
    }
    if (true) {
      devtoolsPerfEnd(instance, type, isSupported() ? perf.now() : Date.now());
    }
  }
  function isSupported() {
    if (supported !== void 0) {
      return supported;
    }
    if (typeof window !== "undefined" && window.performance) {
      supported = true;
      perf = window.performance;
    } else {
      supported = false;
    }
    return supported;
  }
  function initFeatureFlags() {
    const needWarn = [];
    if (false) {
      needWarn.push(`__VUE_OPTIONS_API__`);
      getGlobalThis().__VUE_OPTIONS_API__ = true;
    }
    if (false) {
      needWarn.push(`__VUE_PROD_DEVTOOLS__`);
      getGlobalThis().__VUE_PROD_DEVTOOLS__ = false;
    }
    if (false) {
      needWarn.push(`__VUE_PROD_HYDRATION_MISMATCH_DETAILS__`);
      getGlobalThis().__VUE_PROD_HYDRATION_MISMATCH_DETAILS__ = false;
    }
    if (needWarn.length) {
      const multi = needWarn.length > 1;
      console.warn(
        `Feature flag${multi ? `s` : ``} ${needWarn.join(", ")} ${multi ? `are` : `is`} not explicitly defined. You are running the esm-bundler build of Vue, which expects these compile-time feature flags to be globally injected via the bundler config in order to get better tree-shaking in the production bundle.

For more details, see https://link.vuejs.org/feature-flags.`
      );
    }
  }
  var queuePostRenderEffect = queueEffectWithSuspense;
  function createRenderer(options) {
    return baseCreateRenderer(options);
  }
  function baseCreateRenderer(options, createHydrationFns) {
    {
      initFeatureFlags();
    }
    const target = getGlobalThis();
    target.__VUE__ = true;
    if (true) {
      setDevtoolsHook$1(target.__VUE_DEVTOOLS_GLOBAL_HOOK__, target);
    }
    const {
      insert: hostInsert,
      remove: hostRemove,
      patchProp: hostPatchProp,
      createElement: hostCreateElement,
      createText: hostCreateText,
      createComment: hostCreateComment,
      setText: hostSetText,
      setElementText: hostSetElementText,
      parentNode: hostParentNode,
      nextSibling: hostNextSibling,
      setScopeId: hostSetScopeId = NOOP,
      insertStaticContent: hostInsertStaticContent
    } = options;
    const patch = (n1, n2, container, anchor = null, parentComponent = null, parentSuspense = null, namespace2 = void 0, slotScopeIds = null, optimized = isHmrUpdating ? false : !!n2.dynamicChildren) => {
      if (n1 === n2) {
        return;
      }
      if (n1 && !isSameVNodeType(n1, n2)) {
        anchor = getNextHostNode(n1);
        unmount(n1, parentComponent, parentSuspense, true);
        n1 = null;
      }
      if (n2.patchFlag === -2) {
        optimized = false;
        n2.dynamicChildren = null;
      }
      if (n2.dynamicChildren && n1 && n1.dynamicChildren && n1.dynamicChildren.hasOnce) {
        if (n2.dynamicChildren === EMPTY_ARR) {
          n2.dynamicChildren = [];
        }
        n2.dynamicChildren.hasOnce = true;
      }
      const { type, ref: ref2, shapeFlag } = n2;
      switch (type) {
        case Text:
          processText(n1, n2, container, anchor);
          break;
        case Comment:
          processCommentNode(n1, n2, container, anchor);
          break;
        case Static:
          if (n1 == null) {
            mountStaticNode(n2, container, anchor, namespace2);
          } else if (true) {
            patchStaticNode(n1, n2, container, namespace2);
          }
          break;
        case Fragment:
          processFragment(
            n1,
            n2,
            container,
            anchor,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
          break;
        default:
          if (shapeFlag & 1) {
            processElement(
              n1,
              n2,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
          } else if (shapeFlag & 6) {
            processComponent(
              n1,
              n2,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
          } else if (shapeFlag & 64) {
            type.process(
              n1,
              n2,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized,
              internals
            );
          } else if (shapeFlag & 128) {
            type.process(
              n1,
              n2,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized,
              internals
            );
          } else if (true) {
            warn$1("Invalid VNode type:", type, `(${typeof type})`);
          }
      }
      if (ref2 != null && parentComponent) {
        setRef(ref2, n1 && n1.ref, parentSuspense, n2 || n1, !n2);
      } else if (ref2 == null && n1 && n1.ref != null) {
        setRef(n1.ref, null, parentSuspense, n1, true);
      }
    };
    const processText = (n1, n2, container, anchor) => {
      if (n1 == null) {
        hostInsert(
          n2.el = hostCreateText(n2.children),
          container,
          anchor
        );
      } else {
        const el = n2.el = n1.el;
        if (n2.children !== n1.children) {
          hostSetText(el, n2.children);
        }
      }
    };
    const processCommentNode = (n1, n2, container, anchor) => {
      if (n1 == null) {
        hostInsert(
          n2.el = hostCreateComment(n2.children || ""),
          container,
          anchor
        );
      } else {
        n2.el = n1.el;
      }
    };
    const mountStaticNode = (n2, container, anchor, namespace2) => {
      [n2.el, n2.anchor] = hostInsertStaticContent(
        n2.children,
        container,
        anchor,
        namespace2,
        n2.el,
        n2.anchor
      );
    };
    const patchStaticNode = (n1, n2, container, namespace2) => {
      if (n2.children !== n1.children) {
        const anchor = hostNextSibling(n1.anchor);
        removeStaticNode(n1);
        [n2.el, n2.anchor] = hostInsertStaticContent(
          n2.children,
          container,
          anchor,
          namespace2
        );
      } else {
        n2.el = n1.el;
        n2.anchor = n1.anchor;
      }
    };
    const moveStaticNode = ({ el, anchor }, container, nextSibling) => {
      let next;
      while (el && el !== anchor) {
        next = hostNextSibling(el);
        hostInsert(el, container, nextSibling);
        el = next;
      }
      hostInsert(anchor, container, nextSibling);
    };
    const removeStaticNode = ({ el, anchor }) => {
      let next;
      while (el && el !== anchor) {
        next = hostNextSibling(el);
        hostRemove(el);
        el = next;
      }
      hostRemove(anchor);
    };
    const processElement = (n1, n2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      if (n2.type === "svg") {
        namespace2 = "svg";
      } else if (n2.type === "math") {
        namespace2 = "mathml";
      }
      if (n1 == null) {
        mountElement(
          n2,
          container,
          anchor,
          parentComponent,
          parentSuspense,
          namespace2,
          slotScopeIds,
          optimized
        );
      } else {
        const customElement = n1.el && n1.el._isVueCE ? n1.el : null;
        try {
          if (customElement) {
            customElement._beginPatch();
          }
          patchElement(
            n1,
            n2,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
        } finally {
          if (customElement) {
            customElement._endPatch();
          }
        }
      }
    };
    const mountElement = (vnode, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      let el;
      let vnodeHook;
      const { props, shapeFlag, transition, dirs } = vnode;
      el = vnode.el = hostCreateElement(
        vnode.type,
        namespace2,
        props && props.is,
        props
      );
      if (shapeFlag & 8) {
        hostSetElementText(el, vnode.children);
      } else if (shapeFlag & 16) {
        mountChildren(
          vnode.children,
          el,
          null,
          parentComponent,
          parentSuspense,
          resolveChildrenNamespace(vnode, namespace2),
          slotScopeIds,
          optimized
        );
      }
      if (dirs) {
        invokeDirectiveHook(vnode, null, parentComponent, "created");
      }
      setScopeId(el, vnode, vnode.scopeId, slotScopeIds, parentComponent);
      if (props) {
        for (const key in props) {
          if (key !== "value" && !isReservedProp(key)) {
            hostPatchProp(el, key, null, props[key], namespace2, parentComponent);
          }
        }
        if ("value" in props) {
          hostPatchProp(el, "value", null, props.value, namespace2);
        }
        if (vnodeHook = props.onVnodeBeforeMount) {
          invokeVNodeHook(vnodeHook, parentComponent, vnode);
        }
      }
      if (true) {
        def(el, "__vnode", vnode, true);
        def(el, "__vueParentComponent", parentComponent, true);
      }
      if (dirs) {
        invokeDirectiveHook(vnode, null, parentComponent, "beforeMount");
      }
      const needCallTransitionHooks = needTransition(parentSuspense, transition);
      if (needCallTransitionHooks) {
        transition.beforeEnter(el);
      }
      hostInsert(el, container, anchor);
      if ((vnodeHook = props && props.onVnodeMounted) || needCallTransitionHooks || dirs) {
        const isHmr = isHmrUpdating;
        queuePostRenderEffect(() => {
          let prev;
          if (true) prev = setHmrUpdating(isHmr);
          try {
            vnodeHook && invokeVNodeHook(vnodeHook, parentComponent, vnode);
            needCallTransitionHooks && transition.enter(el);
            dirs && invokeDirectiveHook(vnode, null, parentComponent, "mounted");
          } finally {
            if (true) setHmrUpdating(prev);
          }
        }, parentSuspense);
      }
    };
    const setScopeId = (el, vnode, scopeId, slotScopeIds, parentComponent) => {
      if (scopeId) {
        hostSetScopeId(el, scopeId);
      }
      if (slotScopeIds) {
        for (let i = 0; i < slotScopeIds.length; i++) {
          hostSetScopeId(el, slotScopeIds[i]);
        }
      }
      if (parentComponent) {
        let subTree = parentComponent.subTree;
        if (subTree.patchFlag > 0 && subTree.patchFlag & 2048) {
          subTree = filterSingleRoot(subTree.children) || subTree;
        }
        if (vnode === subTree || isSuspense(subTree.type) && (subTree.ssContent === vnode || subTree.ssFallback === vnode)) {
          const parentVNode = parentComponent.vnode;
          setScopeId(
            el,
            parentVNode,
            parentVNode.scopeId,
            parentVNode.slotScopeIds,
            parentComponent.parent
          );
        }
      }
    };
    const mountChildren = (children2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized, start2 = 0) => {
      for (let i = start2; i < children2.length; i++) {
        const child = children2[i] = optimized ? cloneIfMounted(children2[i]) : normalizeVNode(children2[i]);
        patch(
          null,
          child,
          container,
          anchor,
          parentComponent,
          parentSuspense,
          namespace2,
          slotScopeIds,
          optimized
        );
      }
    };
    const patchElement = (n1, n2, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      const el = n2.el = n1.el;
      if (true) {
        el.__vnode = n2;
      }
      let { patchFlag, dynamicChildren, dirs } = n2;
      patchFlag |= n1.patchFlag & 16;
      const oldProps = n1.props || EMPTY_OBJ;
      const newProps = n2.props || EMPTY_OBJ;
      let vnodeHook;
      parentComponent && toggleRecurse(parentComponent, false);
      if (vnodeHook = newProps.onVnodeBeforeUpdate) {
        invokeVNodeHook(vnodeHook, parentComponent, n2, n1);
      }
      if (dirs) {
        invokeDirectiveHook(n2, n1, parentComponent, "beforeUpdate");
      }
      parentComponent && toggleRecurse(parentComponent, true);
      if (
        // HMR updated, force full diff
        isHmrUpdating || // #6385 the old vnode may be a user-wrapped non-isomorphic block
        // Force full diff when block metadata is unstable.
        dynamicChildren && (!n1.dynamicChildren || n1.dynamicChildren.length !== dynamicChildren.length)
      ) {
        patchFlag = 0;
        optimized = false;
        dynamicChildren = null;
      }
      if (oldProps.innerHTML && newProps.innerHTML == null || oldProps.textContent && newProps.textContent == null) {
        hostSetElementText(el, "");
      }
      if (dynamicChildren) {
        patchBlockChildren(
          n1.dynamicChildren,
          dynamicChildren,
          el,
          parentComponent,
          parentSuspense,
          resolveChildrenNamespace(n2, namespace2),
          slotScopeIds
        );
        if (true) {
          traverseStaticChildren(n1, n2);
        }
      } else if (!optimized) {
        patchChildren(
          n1,
          n2,
          el,
          null,
          parentComponent,
          parentSuspense,
          resolveChildrenNamespace(n2, namespace2),
          slotScopeIds,
          false
        );
      }
      if (patchFlag > 0) {
        if (patchFlag & 16) {
          patchProps(el, oldProps, newProps, parentComponent, namespace2);
        } else {
          if (patchFlag & 2) {
            if (oldProps.class !== newProps.class) {
              hostPatchProp(el, "class", null, newProps.class, namespace2);
            }
          }
          if (patchFlag & 4) {
            hostPatchProp(el, "style", oldProps.style, newProps.style, namespace2);
          }
          if (patchFlag & 8) {
            const propsToUpdate = n2.dynamicProps;
            for (let i = 0; i < propsToUpdate.length; i++) {
              const key = propsToUpdate[i];
              const prev = oldProps[key];
              const next = newProps[key];
              if (next !== prev || key === "value") {
                hostPatchProp(el, key, prev, next, namespace2, parentComponent);
              }
            }
          }
        }
        if (patchFlag & 1) {
          if (n1.children !== n2.children) {
            hostSetElementText(el, n2.children);
          }
        }
      } else if (!optimized && dynamicChildren == null) {
        patchProps(el, oldProps, newProps, parentComponent, namespace2);
      }
      if ((vnodeHook = newProps.onVnodeUpdated) || dirs) {
        queuePostRenderEffect(() => {
          vnodeHook && invokeVNodeHook(vnodeHook, parentComponent, n2, n1);
          dirs && invokeDirectiveHook(n2, n1, parentComponent, "updated");
        }, parentSuspense);
      }
    };
    const patchBlockChildren = (oldChildren, newChildren, fallbackContainer, parentComponent, parentSuspense, namespace2, slotScopeIds) => {
      for (let i = 0; i < newChildren.length; i++) {
        const oldVNode = oldChildren[i];
        const newVNode = newChildren[i];
        const oldEl = oldVNode.el || resolveAsyncComponentPlaceholder(oldVNode);
        const container = (
          // oldVNode may be an errored async setup() component inside Suspense
          // which will not have a mounted element
          oldEl && // - In the case of a Fragment, we need to provide the actual parent
          // of the Fragment itself so it can move its children.
          (oldVNode.type === Fragment || // - In the case of different nodes, there is going to be a replacement
          // which also requires the correct parent container
          !isSameVNodeType(oldVNode, newVNode) || // - In the case of a component, it could contain anything.
          oldVNode.shapeFlag & (6 | 64 | 128)) ? hostParentNode(oldEl) : (
            // In other cases, the parent container is not actually used so we
            // just pass the block element here to avoid a DOM parentNode call.
            fallbackContainer
          )
        );
        patch(
          oldVNode,
          newVNode,
          container,
          null,
          parentComponent,
          parentSuspense,
          namespace2,
          slotScopeIds,
          true
        );
      }
    };
    const patchProps = (el, oldProps, newProps, parentComponent, namespace2) => {
      if (oldProps !== newProps) {
        if (oldProps !== EMPTY_OBJ) {
          for (const key in oldProps) {
            if (!isReservedProp(key) && !(key in newProps)) {
              hostPatchProp(
                el,
                key,
                oldProps[key],
                null,
                namespace2,
                parentComponent
              );
            }
          }
        }
        for (const key in newProps) {
          if (isReservedProp(key)) continue;
          const next = newProps[key];
          const prev = oldProps[key];
          if (next !== prev && key !== "value") {
            hostPatchProp(el, key, prev, next, namespace2, parentComponent);
          }
        }
        if ("value" in newProps) {
          hostPatchProp(el, "value", oldProps.value, newProps.value, namespace2);
        }
      }
    };
    const processFragment = (n1, n2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      const fragmentStartAnchor = n2.el = n1 ? n1.el : hostCreateText("");
      const fragmentEndAnchor = n2.anchor = n1 ? n1.anchor : hostCreateText("");
      let { patchFlag, dynamicChildren, slotScopeIds: fragmentSlotScopeIds } = n2;
      if (
        // #5523 dev root fragment may inherit directives
        isHmrUpdating || patchFlag & 2048
      ) {
        patchFlag = 0;
        optimized = false;
        dynamicChildren = null;
      }
      if (fragmentSlotScopeIds) {
        slotScopeIds = slotScopeIds ? slotScopeIds.concat(fragmentSlotScopeIds) : fragmentSlotScopeIds;
      }
      if (n1 == null) {
        hostInsert(fragmentStartAnchor, container, anchor);
        hostInsert(fragmentEndAnchor, container, anchor);
        mountChildren(
          // #10007
          // such fragment like `<></>` will be compiled into
          // a fragment which doesn't have a children.
          // In this case fallback to an empty array
          n2.children || [],
          container,
          fragmentEndAnchor,
          parentComponent,
          parentSuspense,
          namespace2,
          slotScopeIds,
          optimized
        );
      } else {
        if (patchFlag > 0 && patchFlag & 64 && dynamicChildren && // #2715 the previous fragment could've been a BAILed one as a result
        // of renderSlot() with no valid children
        n1.dynamicChildren && n1.dynamicChildren.length === dynamicChildren.length) {
          patchBlockChildren(
            n1.dynamicChildren,
            dynamicChildren,
            container,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds
          );
          if (true) {
            traverseStaticChildren(n1, n2);
          } else if (
            // #2080 if the stable fragment has a key, it's a <template v-for> that may
            //  get moved around. Make sure all root level vnodes inherit el.
            // #2134 or if it's a component root, it may also get moved around
            // as the component is being moved.
            n2.key != null || parentComponent && n2 === parentComponent.subTree
          ) {
            traverseStaticChildren(
              n1,
              n2,
              true
              /* shallow */
            );
          }
        } else {
          patchChildren(
            n1,
            n2,
            container,
            fragmentEndAnchor,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
        }
      }
    };
    const processComponent = (n1, n2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      n2.slotScopeIds = slotScopeIds;
      if (n1 == null) {
        if (n2.shapeFlag & 512) {
          parentComponent.ctx.activate(
            n2,
            container,
            anchor,
            namespace2,
            optimized
          );
        } else {
          mountComponent(
            n2,
            container,
            anchor,
            parentComponent,
            parentSuspense,
            namespace2,
            optimized
          );
        }
      } else {
        updateComponent(n1, n2, optimized);
      }
    };
    const mountComponent = (initialVNode, container, anchor, parentComponent, parentSuspense, namespace2, optimized) => {
      const instance = initialVNode.component = createComponentInstance(
        initialVNode,
        parentComponent,
        parentSuspense
      );
      if (instance.type.__hmrId) {
        registerHMR(instance);
      }
      if (true) {
        pushWarningContext(initialVNode);
        startMeasure(instance, `mount`);
      }
      if (isKeepAlive(initialVNode)) {
        instance.ctx.renderer = internals;
      }
      {
        if (true) {
          startMeasure(instance, `init`);
        }
        setupComponent(instance, false, optimized);
        if (true) {
          endMeasure(instance, `init`);
        }
      }
      if (isHmrUpdating) initialVNode.el = null;
      if (instance.asyncDep) {
        parentSuspense && parentSuspense.registerDep(
          instance,
          setupRenderEffect,
          optimized,
          namespace2
        );
        if (!initialVNode.el) {
          const placeholder = instance.subTree = createVNode(Comment);
          processCommentNode(null, placeholder, container, anchor);
          initialVNode.placeholder = placeholder.el;
        }
      } else {
        setupRenderEffect(
          instance,
          initialVNode,
          container,
          anchor,
          parentSuspense,
          namespace2,
          optimized
        );
      }
      if (true) {
        popWarningContext();
        endMeasure(instance, `mount`);
      }
    };
    const updateComponent = (n1, n2, optimized) => {
      const instance = n2.component = n1.component;
      if (shouldUpdateComponent(n1, n2, optimized)) {
        if (instance.asyncDep && !instance.asyncResolved) {
          if (true) {
            pushWarningContext(n2);
          }
          n2.el = n1.el;
          n2.placeholder = n1.placeholder;
          updateComponentPreRender(instance, n2, optimized);
          if (true) {
            popWarningContext();
          }
          return;
        } else {
          instance.next = n2;
          instance.update();
        }
      } else {
        n2.el = n1.el;
        n2.placeholder = n1.placeholder;
        instance.vnode = n2;
      }
    };
    const setupRenderEffect = (instance, initialVNode, container, anchor, parentSuspense, namespace2, optimized) => {
      const componentUpdateFn = () => {
        if (!instance.isMounted) {
          let vnodeHook;
          const { el, props } = initialVNode;
          const { bm, m, parent, root: root2, type } = instance;
          const isAsyncWrapperVNode = isAsyncWrapper(initialVNode);
          toggleRecurse(instance, false);
          if (bm) {
            invokeArrayFns(bm);
          }
          if (!isAsyncWrapperVNode && (vnodeHook = props && props.onVnodeBeforeMount)) {
            invokeVNodeHook(vnodeHook, parent, initialVNode);
          }
          toggleRecurse(instance, true);
          if (el && hydrateNode) {
            const hydrateSubTree = () => {
              if (true) {
                startMeasure(instance, `render`);
              }
              instance.subTree = renderComponentRoot(instance);
              if (true) {
                endMeasure(instance, `render`);
              }
              if (true) {
                startMeasure(instance, `hydrate`);
              }
              hydrateNode(
                el,
                instance.subTree,
                instance,
                parentSuspense,
                null
              );
              if (true) {
                endMeasure(instance, `hydrate`);
              }
            };
            if (isAsyncWrapperVNode && type.__asyncHydrate) {
              type.__asyncHydrate(
                el,
                instance,
                hydrateSubTree
              );
            } else {
              hydrateSubTree();
            }
          } else {
            if (root2.ce && root2.ce._hasShadowRoot()) {
              root2.ce._injectChildStyle(
                type,
                instance.parent ? instance.parent.type : void 0
              );
            }
            if (true) {
              startMeasure(instance, `render`);
            }
            const subTree = instance.subTree = renderComponentRoot(instance);
            if (true) {
              endMeasure(instance, `render`);
            }
            if (true) {
              startMeasure(instance, `patch`);
            }
            patch(
              null,
              subTree,
              container,
              anchor,
              instance,
              parentSuspense,
              namespace2
            );
            if (true) {
              endMeasure(instance, `patch`);
            }
            initialVNode.el = subTree.el;
          }
          if (m) {
            queuePostRenderEffect(m, parentSuspense);
          }
          if (!isAsyncWrapperVNode && (vnodeHook = props && props.onVnodeMounted)) {
            const scopedInitialVNode = initialVNode;
            queuePostRenderEffect(
              () => invokeVNodeHook(vnodeHook, parent, scopedInitialVNode),
              parentSuspense
            );
          }
          if (initialVNode.shapeFlag & 256 || parent && isAsyncWrapper(parent.vnode) && parent.vnode.shapeFlag & 256) {
            instance.a && queuePostRenderEffect(instance.a, parentSuspense);
          }
          instance.isMounted = true;
          if (true) {
            devtoolsComponentAdded(instance);
          }
          initialVNode = container = anchor = null;
        } else {
          let { next, bu, u, parent, vnode } = instance;
          {
            const nonHydratedAsyncRoot = locateNonHydratedAsyncRoot(instance);
            if (nonHydratedAsyncRoot) {
              if (next) {
                next.el = vnode.el;
                updateComponentPreRender(instance, next, optimized);
              }
              nonHydratedAsyncRoot.asyncDep.then(() => {
                queuePostRenderEffect(() => {
                  if (!instance.isUnmounted) update();
                }, parentSuspense);
              });
              return;
            }
          }
          let originNext = next;
          let vnodeHook;
          if (true) {
            pushWarningContext(next || instance.vnode);
          }
          toggleRecurse(instance, false);
          if (next) {
            next.el = vnode.el;
            updateComponentPreRender(instance, next, optimized);
          } else {
            next = vnode;
          }
          if (bu) {
            invokeArrayFns(bu);
          }
          if (vnodeHook = next.props && next.props.onVnodeBeforeUpdate) {
            invokeVNodeHook(vnodeHook, parent, next, vnode);
          }
          toggleRecurse(instance, true);
          if (true) {
            startMeasure(instance, `render`);
          }
          const nextTree = renderComponentRoot(instance);
          if (true) {
            endMeasure(instance, `render`);
          }
          const prevTree = instance.subTree;
          instance.subTree = nextTree;
          if (true) {
            startMeasure(instance, `patch`);
          }
          patch(
            prevTree,
            nextTree,
            // parent may have changed if it's in a teleport
            hostParentNode(prevTree.el),
            // anchor may have changed if it's in a fragment
            getNextHostNode(prevTree),
            instance,
            parentSuspense,
            namespace2
          );
          if (true) {
            endMeasure(instance, `patch`);
          }
          next.el = nextTree.el;
          if (originNext === null) {
            updateHOCHostEl(instance, nextTree.el);
          }
          if (u) {
            queuePostRenderEffect(u, parentSuspense);
          }
          if (vnodeHook = next.props && next.props.onVnodeUpdated) {
            queuePostRenderEffect(
              () => invokeVNodeHook(vnodeHook, parent, next, vnode),
              parentSuspense
            );
          }
          if (true) {
            devtoolsComponentUpdated(instance);
          }
          if (true) {
            popWarningContext();
          }
        }
      };
      instance.scope.on();
      const effect2 = instance.effect = new ReactiveEffect(componentUpdateFn);
      instance.scope.off();
      const update = instance.update = effect2.run.bind(effect2);
      const job = instance.job = effect2.runIfDirty.bind(effect2);
      job.i = instance;
      job.id = instance.uid;
      effect2.scheduler = () => queueJob(job);
      toggleRecurse(instance, true);
      if (true) {
        effect2.onTrack = instance.rtc ? (e) => invokeArrayFns(instance.rtc, e) : void 0;
        effect2.onTrigger = instance.rtg ? (e) => invokeArrayFns(instance.rtg, e) : void 0;
      }
      update();
    };
    const updateComponentPreRender = (instance, nextVNode, optimized) => {
      nextVNode.component = instance;
      const prevProps = instance.vnode.props;
      instance.vnode = nextVNode;
      instance.next = null;
      updateProps(instance, nextVNode.props, prevProps, optimized);
      updateSlots(instance, nextVNode.children, optimized);
      pauseTracking();
      flushPreFlushCbs(instance);
      resetTracking();
    };
    const patchChildren = (n1, n2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized = false) => {
      const c1 = n1 && n1.children;
      const prevShapeFlag = n1 ? n1.shapeFlag : 0;
      const c2 = n2.children;
      const { patchFlag, shapeFlag } = n2;
      if (patchFlag > 0) {
        if (patchFlag & 128) {
          patchKeyedChildren(
            c1,
            c2,
            container,
            anchor,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
          return;
        } else if (patchFlag & 256) {
          patchUnkeyedChildren(
            c1,
            c2,
            container,
            anchor,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
          return;
        }
      }
      if (shapeFlag & 8) {
        if (prevShapeFlag & 16) {
          unmountChildren(c1, parentComponent, parentSuspense);
        }
        if (c2 !== c1) {
          hostSetElementText(container, c2);
        }
      } else {
        if (prevShapeFlag & 16) {
          if (shapeFlag & 16) {
            patchKeyedChildren(
              c1,
              c2,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
          } else {
            unmountChildren(c1, parentComponent, parentSuspense, true);
          }
        } else {
          if (prevShapeFlag & 8) {
            hostSetElementText(container, "");
          }
          if (shapeFlag & 16) {
            mountChildren(
              c2,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
          }
        }
      }
    };
    const patchUnkeyedChildren = (c1, c2, container, anchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      c1 = c1 || EMPTY_ARR;
      c2 = c2 || EMPTY_ARR;
      const oldLength = c1.length;
      const newLength = c2.length;
      const commonLength = Math.min(oldLength, newLength);
      let i;
      for (i = 0; i < commonLength; i++) {
        const nextChild = c2[i] = optimized ? cloneIfMounted(c2[i]) : normalizeVNode(c2[i]);
        patch(
          c1[i],
          nextChild,
          container,
          null,
          parentComponent,
          parentSuspense,
          namespace2,
          slotScopeIds,
          optimized
        );
      }
      if (oldLength > newLength) {
        unmountChildren(
          c1,
          parentComponent,
          parentSuspense,
          true,
          false,
          commonLength
        );
      } else {
        mountChildren(
          c2,
          container,
          anchor,
          parentComponent,
          parentSuspense,
          namespace2,
          slotScopeIds,
          optimized,
          commonLength
        );
      }
    };
    const patchKeyedChildren = (c1, c2, container, parentAnchor, parentComponent, parentSuspense, namespace2, slotScopeIds, optimized) => {
      let i = 0;
      const l2 = c2.length;
      let e1 = c1.length - 1;
      let e2 = l2 - 1;
      while (i <= e1 && i <= e2) {
        const n1 = c1[i];
        const n2 = c2[i] = optimized ? cloneIfMounted(c2[i]) : normalizeVNode(c2[i]);
        if (isSameVNodeType(n1, n2)) {
          patch(
            n1,
            n2,
            container,
            null,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
        } else {
          break;
        }
        i++;
      }
      while (i <= e1 && i <= e2) {
        const n1 = c1[e1];
        const n2 = c2[e2] = optimized ? cloneIfMounted(c2[e2]) : normalizeVNode(c2[e2]);
        if (isSameVNodeType(n1, n2)) {
          patch(
            n1,
            n2,
            container,
            null,
            parentComponent,
            parentSuspense,
            namespace2,
            slotScopeIds,
            optimized
          );
        } else {
          break;
        }
        e1--;
        e2--;
      }
      if (i > e1) {
        if (i <= e2) {
          const nextPos = e2 + 1;
          const anchor = nextPos < l2 ? c2[nextPos].el : parentAnchor;
          while (i <= e2) {
            patch(
              null,
              c2[i] = optimized ? cloneIfMounted(c2[i]) : normalizeVNode(c2[i]),
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
            i++;
          }
        }
      } else if (i > e2) {
        while (i <= e1) {
          unmount(c1[i], parentComponent, parentSuspense, true);
          i++;
        }
      } else {
        const s1 = i;
        const s2 = i;
        const keyToNewIndexMap = /* @__PURE__ */ new Map();
        for (i = s2; i <= e2; i++) {
          const nextChild = c2[i] = optimized ? cloneIfMounted(c2[i]) : normalizeVNode(c2[i]);
          if (nextChild.key != null) {
            if (keyToNewIndexMap.has(nextChild.key)) {
              warn$1(
                `Duplicate keys found during update:`,
                JSON.stringify(nextChild.key),
                `Make sure keys are unique.`
              );
            }
            keyToNewIndexMap.set(nextChild.key, i);
          }
        }
        let j;
        let patched = 0;
        const toBePatched = e2 - s2 + 1;
        let moved = false;
        let maxNewIndexSoFar = 0;
        const newIndexToOldIndexMap = new Array(toBePatched);
        for (i = 0; i < toBePatched; i++) newIndexToOldIndexMap[i] = 0;
        for (i = s1; i <= e1; i++) {
          const prevChild = c1[i];
          if (patched >= toBePatched) {
            unmount(prevChild, parentComponent, parentSuspense, true);
            continue;
          }
          let newIndex;
          if (prevChild.key != null) {
            newIndex = keyToNewIndexMap.get(prevChild.key);
          } else {
            for (j = s2; j <= e2; j++) {
              if (newIndexToOldIndexMap[j - s2] === 0 && isSameVNodeType(prevChild, c2[j])) {
                newIndex = j;
                break;
              }
            }
          }
          if (newIndex === void 0) {
            unmount(prevChild, parentComponent, parentSuspense, true);
          } else {
            newIndexToOldIndexMap[newIndex - s2] = i + 1;
            if (newIndex >= maxNewIndexSoFar) {
              maxNewIndexSoFar = newIndex;
            } else {
              moved = true;
            }
            patch(
              prevChild,
              c2[newIndex],
              container,
              null,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
            patched++;
          }
        }
        const increasingNewIndexSequence = moved ? getSequence(newIndexToOldIndexMap) : EMPTY_ARR;
        j = increasingNewIndexSequence.length - 1;
        for (i = toBePatched - 1; i >= 0; i--) {
          const nextIndex = s2 + i;
          const nextChild = c2[nextIndex];
          const anchorVNode = c2[nextIndex + 1];
          const anchor = nextIndex + 1 < l2 ? (
            // #13559, #14173 fallback to el placeholder for unresolved async component
            anchorVNode.el || resolveAsyncComponentPlaceholder(anchorVNode)
          ) : parentAnchor;
          if (newIndexToOldIndexMap[i] === 0) {
            patch(
              null,
              nextChild,
              container,
              anchor,
              parentComponent,
              parentSuspense,
              namespace2,
              slotScopeIds,
              optimized
            );
          } else if (moved) {
            if (j < 0 || i !== increasingNewIndexSequence[j]) {
              move(nextChild, container, anchor, 2);
            } else {
              j--;
            }
          }
        }
      }
    };
    const move = (vnode, container, anchor, moveType, parentSuspense = null) => {
      const { el, type, transition, children: children2, shapeFlag } = vnode;
      if (shapeFlag & 6) {
        move(vnode.component.subTree, container, anchor, moveType);
        return;
      }
      if (shapeFlag & 128) {
        vnode.suspense.move(container, anchor, moveType);
        return;
      }
      if (shapeFlag & 64) {
        type.move(vnode, container, anchor, internals);
        return;
      }
      if (type === Fragment) {
        hostInsert(el, container, anchor);
        for (let i = 0; i < children2.length; i++) {
          move(children2[i], container, anchor, moveType);
        }
        hostInsert(vnode.anchor, container, anchor);
        return;
      }
      if (type === Static) {
        moveStaticNode(vnode, container, anchor);
        return;
      }
      const needTransition2 = moveType !== 2 && shapeFlag & 1 && transition;
      if (needTransition2) {
        if (moveType === 0) {
          if (transition.persisted && !el[leaveCbKey]) {
            hostInsert(el, container, anchor);
          } else {
            transition.beforeEnter(el);
            hostInsert(el, container, anchor);
            queuePostRenderEffect(() => transition.enter(el), parentSuspense);
          }
        } else {
          const { leave, delayLeave, afterLeave } = transition;
          const remove22 = () => {
            if (vnode.ctx.isUnmounted) {
              hostRemove(el);
            } else {
              hostInsert(el, container, anchor);
            }
          };
          const performLeave = () => {
            const wasLeaving = el._isLeaving || !!el[leaveCbKey];
            if (el._isLeaving) {
              el[leaveCbKey](
                true
                /* cancelled */
              );
            }
            if (transition.persisted && !wasLeaving) {
              remove22();
            } else {
              leave(el, () => {
                remove22();
                afterLeave && afterLeave();
              });
            }
          };
          if (delayLeave) {
            delayLeave(el, remove22, performLeave);
          } else {
            performLeave();
          }
        }
      } else {
        hostInsert(el, container, anchor);
      }
    };
    const unmount = (vnode, parentComponent, parentSuspense, doRemove = false, optimized = false) => {
      const {
        type,
        props,
        ref: ref2,
        children: children2,
        dynamicChildren,
        shapeFlag,
        patchFlag,
        dirs,
        cacheIndex,
        memo
      } = vnode;
      if (patchFlag === -2 || dynamicChildren && dynamicChildren.hasOnce) {
        optimized = false;
      }
      if (ref2 != null) {
        pauseTracking();
        setRef(ref2, null, parentSuspense, vnode, true);
        resetTracking();
      }
      if (cacheIndex != null && (!vnode.ctx || vnode.ctx === parentComponent)) {
        parentComponent.renderCache[cacheIndex] = void 0;
      }
      if (shapeFlag & 256) {
        parentComponent.ctx.deactivate(vnode);
        return;
      }
      const shouldInvokeDirs = shapeFlag & 1 && dirs;
      const shouldInvokeVnodeHook = !isAsyncWrapper(vnode);
      let vnodeHook;
      if (shouldInvokeVnodeHook && (vnodeHook = props && props.onVnodeBeforeUnmount)) {
        invokeVNodeHook(vnodeHook, parentComponent, vnode);
      }
      if (shapeFlag & 6) {
        unmountComponent(vnode.component, parentSuspense, doRemove);
      } else {
        if (shapeFlag & 128) {
          vnode.suspense.unmount(parentSuspense, doRemove);
          return;
        }
        if (shouldInvokeDirs) {
          invokeDirectiveHook(vnode, null, parentComponent, "beforeUnmount");
        }
        if (shapeFlag & 64) {
          vnode.type.remove(
            vnode,
            parentComponent,
            parentSuspense,
            internals,
            doRemove
          );
        } else if (dynamicChildren && // #5154
        // when v-once is used inside a block, setBlockTracking(-1) marks the
        // parent block with hasOnce: true
        // so that it doesn't take the fast path during unmount - otherwise
        // components nested in v-once are never unmounted.
        !dynamicChildren.hasOnce && // #1153: fast path should not be taken for non-stable (v-for) fragments
        (type !== Fragment || patchFlag > 0 && patchFlag & 64)) {
          unmountChildren(
            dynamicChildren,
            parentComponent,
            parentSuspense,
            false,
            true
          );
        } else if (type === Fragment && patchFlag & (128 | 256) || !optimized && shapeFlag & 16) {
          unmountChildren(children2, parentComponent, parentSuspense);
        }
        if (doRemove) {
          remove3(vnode);
        }
      }
      const shouldInvalidateMemo = memo != null && cacheIndex == null;
      if (shouldInvokeVnodeHook && (vnodeHook = props && props.onVnodeUnmounted) || shouldInvokeDirs || shouldInvalidateMemo) {
        queuePostRenderEffect(() => {
          vnodeHook && invokeVNodeHook(vnodeHook, parentComponent, vnode);
          shouldInvokeDirs && invokeDirectiveHook(vnode, null, parentComponent, "unmounted");
          if (shouldInvalidateMemo) {
            vnode.el = null;
          }
        }, parentSuspense);
      }
    };
    const remove3 = (vnode) => {
      const { type, el, anchor, transition } = vnode;
      const isFragment = type === Fragment;
      if (isFragment && !transition) {
        removeFragment(el, anchor);
        return;
      }
      if (type === Static) {
        removeStaticNode(vnode);
        if (transition && !transition.persisted && transition.afterLeave) {
          transition.afterLeave();
        }
        return;
      }
      const leavingNode = isFragment ? getTransitionRoot(vnode) : vnode;
      const performRemove = () => {
        if (isFragment) {
          if (hostParentNode(el)) {
            removeFragment(el, anchor);
          }
        } else {
          hostRemove(el);
        }
        if (transition && !transition.persisted && transition.afterLeave) {
          transition.afterLeave();
        }
      };
      if (leavingNode && leavingNode.shapeFlag & 1 && transition && !transition.persisted) {
        const { leave, delayLeave } = transition;
        const leavingEl = leavingNode.el;
        const performLeave = () => leave(leavingEl, performRemove);
        if (delayLeave) {
          delayLeave(leavingEl, performRemove, performLeave);
        } else {
          performLeave();
        }
      } else {
        performRemove();
      }
    };
    const removeFragment = (cur, end) => {
      let next;
      while (cur !== end) {
        next = hostNextSibling(cur);
        hostRemove(cur);
        cur = next;
      }
      hostRemove(end);
    };
    const unmountComponent = (instance, parentSuspense, doRemove) => {
      if (instance.type.__hmrId) {
        unregisterHMR(instance);
      }
      const { bum, scope, job, subTree, um, m, a } = instance;
      invalidateMount(m);
      invalidateMount(a);
      if (bum) {
        invokeArrayFns(bum);
      }
      scope.stop();
      if (job) {
        job.flags |= 8;
        unmount(subTree, instance, parentSuspense, doRemove);
      } else if (instance.vnode.el && subTree) {
        subTree.transition = instance.vnode.transition;
        unmount(subTree, instance, parentSuspense, doRemove);
      }
      if (um) {
        queuePostRenderEffect(um, parentSuspense);
      }
      queuePostRenderEffect(() => {
        instance.isUnmounted = true;
      }, parentSuspense);
      if (true) {
        devtoolsComponentRemoved(instance);
      }
    };
    const unmountChildren = (children2, parentComponent, parentSuspense, doRemove = false, optimized = false, start2 = 0) => {
      for (let i = start2; i < children2.length; i++) {
        unmount(children2[i], parentComponent, parentSuspense, doRemove, optimized);
      }
    };
    const getNextHostNode = (vnode) => {
      if (vnode.shapeFlag & 6) {
        return getNextHostNode(vnode.component.subTree);
      }
      if (vnode.shapeFlag & 128) {
        return vnode.suspense.next();
      }
      const el = hostNextSibling(vnode.anchor || vnode.el);
      const teleportEnd = el && el[TeleportEndKey];
      return teleportEnd ? hostNextSibling(teleportEnd) : el;
    };
    const render2 = (vnode, container, namespace2) => {
      const minUid = vnode && container._vnode ? void 0 : vnode && vnode.component ? vnode.component.uid : getNextUid();
      flushOnAppMount(() => {
        if (vnode == null) {
          if (container._vnode) {
            unmount(container._vnode, null, null, true);
          }
        } else {
          patch(
            container._vnode || null,
            vnode,
            container,
            null,
            null,
            null,
            namespace2
          );
        }
        container._vnode = vnode;
      }, minUid);
    };
    const internals = {
      p: patch,
      um: unmount,
      m: move,
      r: remove3,
      mt: mountComponent,
      mc: mountChildren,
      pc: patchChildren,
      pbc: patchBlockChildren,
      n: getNextHostNode,
      o: options
    };
    let hydrate;
    let hydrateNode;
    if (createHydrationFns) {
      [hydrate, hydrateNode] = createHydrationFns(
        internals
      );
    }
    return {
      render: render2,
      hydrate,
      createApp: createAppAPI(render2, hydrate)
    };
  }
  function resolveChildrenNamespace({ type, props }, currentNamespace) {
    return currentNamespace === "svg" && type === "foreignObject" || currentNamespace === "mathml" && type === "annotation-xml" && props && props.encoding && props.encoding.includes("html") ? void 0 : currentNamespace;
  }
  function toggleRecurse({ effect: effect2, job }, allowed) {
    if (allowed) {
      effect2.flags |= 32;
      job.flags |= 4;
    } else {
      effect2.flags &= -33;
      job.flags &= -5;
    }
  }
  function needTransition(parentSuspense, transition) {
    return (!parentSuspense || parentSuspense && !parentSuspense.pendingBranch) && transition && !transition.persisted;
  }
  function traverseStaticChildren(n1, n2, shallow = false) {
    const ch1 = n1.children;
    const ch2 = n2.children;
    if (isArray(ch1) && isArray(ch2)) {
      for (let i = 0; i < ch1.length; i++) {
        const c1 = ch1[i];
        let c2 = ch2[i];
        if (c2.shapeFlag & 1 && !c2.dynamicChildren) {
          if (c2.patchFlag <= 0 || c2.patchFlag === 32) {
            c2 = ch2[i] = cloneIfMounted(ch2[i]);
            c2.el = c1.el;
          }
          if (!shallow && c2.patchFlag !== -2)
            traverseStaticChildren(c1, c2);
        }
        if (c2.type === Text) {
          if (c2.patchFlag === -1) {
            c2 = ch2[i] = cloneIfMounted(c2);
          }
          c2.el = c1.el;
        }
        if (c2.type === Comment && !c2.el) {
          c2.el = c1.el;
        }
        if (true) {
          c2.el && (c2.el.__vnode = c2);
        }
      }
    }
  }
  function getSequence(arr) {
    const p2 = arr.slice();
    const result = [0];
    let i, j, u, v, c;
    const len = arr.length;
    for (i = 0; i < len; i++) {
      const arrI = arr[i];
      if (arrI !== 0) {
        j = result[result.length - 1];
        if (arr[j] < arrI) {
          p2[i] = j;
          result.push(i);
          continue;
        }
        u = 0;
        v = result.length - 1;
        while (u < v) {
          c = u + v >> 1;
          if (arr[result[c]] < arrI) {
            u = c + 1;
          } else {
            v = c;
          }
        }
        if (arrI < arr[result[u]]) {
          if (u > 0) {
            p2[i] = result[u - 1];
          }
          result[u] = i;
        }
      }
    }
    u = result.length;
    v = result[u - 1];
    while (u-- > 0) {
      result[u] = v;
      v = p2[v];
    }
    return result;
  }
  function locateNonHydratedAsyncRoot(instance) {
    const subComponent = instance.subTree.component;
    if (subComponent) {
      if (subComponent.asyncDep && !subComponent.asyncResolved) {
        return subComponent;
      } else {
        return locateNonHydratedAsyncRoot(subComponent);
      }
    }
  }
  function invalidateMount(hooks) {
    if (hooks) {
      for (let i = 0; i < hooks.length; i++)
        hooks[i].flags |= 8;
    }
  }
  function resolveAsyncComponentPlaceholder(anchorVnode) {
    if (anchorVnode.placeholder) {
      return anchorVnode.placeholder;
    }
    const instance = anchorVnode.component;
    if (instance) {
      return resolveAsyncComponentPlaceholder(instance.subTree);
    }
    return null;
  }
  var isSuspense = (type) => type.__isSuspense;
  function queueEffectWithSuspense(fn, suspense) {
    if (suspense && suspense.pendingBranch) {
      if (isArray(fn)) {
        suspense.effects.push(...fn);
      } else {
        suspense.effects.push(fn);
      }
    } else {
      queuePostFlushCb(fn);
    }
  }
  var Fragment = /* @__PURE__ */ Symbol.for("v-fgt");
  var Text = /* @__PURE__ */ Symbol.for("v-txt");
  var Comment = /* @__PURE__ */ Symbol.for("v-cmt");
  var Static = /* @__PURE__ */ Symbol.for("v-stc");
  var blockStack = [];
  var currentBlock = null;
  function openBlock(disableTracking = false) {
    blockStack.push(currentBlock = disableTracking ? null : []);
  }
  function closeBlock() {
    blockStack.pop();
    currentBlock = blockStack[blockStack.length - 1] || null;
  }
  var isBlockTreeEnabled = 1;
  function setBlockTracking(value, inVOnce = false) {
    isBlockTreeEnabled += value;
    if (value < 0 && currentBlock && inVOnce) {
      currentBlock.hasOnce = true;
    }
  }
  function setupBlock(vnode) {
    vnode.dynamicChildren = isBlockTreeEnabled > 0 ? currentBlock || EMPTY_ARR : null;
    closeBlock();
    if (isBlockTreeEnabled > 0 && currentBlock) {
      currentBlock.push(vnode);
    }
    return vnode;
  }
  function createElementBlock(type, props, children2, patchFlag, dynamicProps, shapeFlag) {
    return setupBlock(
      createBaseVNode(
        type,
        props,
        children2,
        patchFlag,
        dynamicProps,
        shapeFlag,
        true
      )
    );
  }
  function createBlock(type, props, children2, patchFlag, dynamicProps) {
    return setupBlock(
      createVNode(
        type,
        props,
        children2,
        patchFlag,
        dynamicProps,
        true
      )
    );
  }
  function isVNode(value) {
    return value ? value.__v_isVNode === true : false;
  }
  function isSameVNodeType(n1, n2) {
    if (n2.shapeFlag & 6 && n1.component) {
      const dirtyInstances = hmrDirtyComponents.get(n2.type);
      if (dirtyInstances && dirtyInstances.has(n1.component)) {
        n1.shapeFlag &= -257;
        n2.shapeFlag &= -513;
        return false;
      }
    }
    return n1.type === n2.type && n1.key === n2.key;
  }
  var vnodeArgsTransformer;
  var createVNodeWithArgsTransform = (...args) => {
    return _createVNode(
      ...vnodeArgsTransformer ? vnodeArgsTransformer(args, currentRenderingInstance) : args
    );
  };
  var normalizeKey = ({ key }) => key != null ? key : null;
  var normalizeRef = ({
    ref: ref2,
    ref_key,
    ref_for
  }) => {
    if (typeof ref2 === "number") {
      ref2 = "" + ref2;
    }
    return ref2 != null ? isString(ref2) || isRef2(ref2) || isFunction(ref2) ? { i: currentRenderingInstance, r: ref2, k: ref_key, f: !!ref_for } : ref2 : null;
  };
  function createBaseVNode(type, props = null, children2 = null, patchFlag = 0, dynamicProps = null, shapeFlag = type === Fragment ? 0 : 1, isBlockNode = false, needFullChildrenNormalization = false) {
    const vnode = {
      __v_isVNode: true,
      __v_skip: true,
      type,
      props,
      key: props && normalizeKey(props),
      ref: props && normalizeRef(props),
      scopeId: currentScopeId,
      slotScopeIds: null,
      children: children2,
      component: null,
      suspense: null,
      ssContent: null,
      ssFallback: null,
      dirs: null,
      transition: null,
      el: null,
      anchor: null,
      target: null,
      targetStart: null,
      targetAnchor: null,
      staticCount: 0,
      shapeFlag,
      patchFlag,
      dynamicProps,
      dynamicChildren: null,
      appContext: null,
      ctx: currentRenderingInstance
    };
    if (needFullChildrenNormalization) {
      normalizeChildren(vnode, children2);
      if (shapeFlag & 128) {
        type.normalize(vnode);
      }
    } else if (children2) {
      vnode.shapeFlag |= isString(children2) ? 8 : 16;
    }
    if (vnode.key !== vnode.key) {
      warn$1(`VNode created with invalid key (NaN). VNode type:`, vnode.type);
    }
    if (props && vnode.shapeFlag & 1) {
      const overwritingProp = props.innerHTML != null ? "innerHTML" : props.textContent != null ? "textContent" : null;
      if (overwritingProp && hasContentChildren(vnode.children)) {
        warn$1(
          `The \`${overwritingProp}\` prop on <${vnode.type}> will override its children. Remove either the \`${overwritingProp}\` prop or the children.`
        );
      }
    }
    if (isBlockTreeEnabled > 0 && // avoid a block node from tracking itself
    !isBlockNode && // has current parent block
    currentBlock && // presence of a patch flag indicates this node needs patching on updates.
    // component nodes also should always be patched, because even if the
    // component doesn't need to update, it needs to persist the instance on to
    // the next vnode so that it can be properly unmounted later.
    (vnode.patchFlag > 0 || shapeFlag & 6) && // the EVENTS flag is only for hydration and if it is the only flag, the
    // vnode should not be considered dynamic due to handler caching.
    vnode.patchFlag !== 32) {
      currentBlock.push(vnode);
    }
    return vnode;
  }
  function hasContentChildren(children2) {
    if (isString(children2)) return children2 !== "";
    if (isArray(children2)) return children2.length > 0;
    return false;
  }
  var createVNode = true ? createVNodeWithArgsTransform : _createVNode;
  function _createVNode(type, props = null, children2 = null, patchFlag = 0, dynamicProps = null, isBlockNode = false) {
    if (!type || type === NULL_DYNAMIC_COMPONENT) {
      if (!type) {
        warn$1(`Invalid vnode type when creating vnode: ${type}.`);
      }
      type = Comment;
    }
    if (isVNode(type)) {
      const cloned = cloneVNode(
        type,
        props,
        true
        /* mergeRef: true */
      );
      if (children2) {
        normalizeChildren(cloned, children2);
      }
      if (isBlockTreeEnabled > 0 && !isBlockNode && currentBlock) {
        if (cloned.shapeFlag & 6) {
          currentBlock[currentBlock.indexOf(type)] = cloned;
        } else {
          currentBlock.push(cloned);
        }
      }
      cloned.patchFlag = -2;
      return cloned;
    }
    if (isClassComponent(type)) {
      type = type.__vccOpts;
    }
    if (props) {
      props = guardReactiveProps(props);
      let { class: klass, style } = props;
      if (klass && !isString(klass)) {
        props.class = normalizeClass(klass);
      }
      if (isObject(style)) {
        if (isProxy(style) && !isArray(style)) {
          style = extend({}, style);
        }
        props.style = normalizeStyle(style);
      }
    }
    const shapeFlag = isString(type) ? 1 : isSuspense(type) ? 128 : isTeleport(type) ? 64 : isObject(type) ? 4 : isFunction(type) ? 2 : 0;
    if (shapeFlag & 4 && isProxy(type)) {
      type = toRaw(type);
      warn$1(
        `Vue received a Component that was made a reactive object. This can lead to unnecessary performance overhead and should be avoided by marking the component with \`markRaw\` or using \`shallowRef\` instead of \`ref\`.`,
        `
Component that was made reactive: `,
        type
      );
    }
    return createBaseVNode(
      type,
      props,
      children2,
      patchFlag,
      dynamicProps,
      shapeFlag,
      isBlockNode,
      true
    );
  }
  function guardReactiveProps(props) {
    if (!props) return null;
    return isProxy(props) || isInternalObject(props) ? extend({}, props) : props;
  }
  function cloneVNode(vnode, extraProps, mergeRef = false, cloneTransition = false) {
    const { props, ref: ref2, patchFlag, children: children2, transition } = vnode;
    const mergedProps = extraProps ? mergeProps(props || {}, extraProps) : props;
    const cloned = {
      __v_isVNode: true,
      __v_skip: true,
      type: vnode.type,
      props: mergedProps,
      key: mergedProps && normalizeKey(mergedProps),
      ref: extraProps && extraProps.ref ? (
        // #2078 in the case of <component :is="vnode" ref="extra"/>
        // if the vnode itself already has a ref, cloneVNode will need to merge
        // the refs so the single vnode can be set on multiple refs
        mergeRef && ref2 ? isArray(ref2) ? ref2.concat(normalizeRef(extraProps)) : [ref2, normalizeRef(extraProps)] : normalizeRef(extraProps)
      ) : ref2,
      scopeId: vnode.scopeId,
      slotScopeIds: vnode.slotScopeIds,
      children: patchFlag === -1 && isArray(children2) ? children2.map(deepCloneVNode) : children2,
      target: vnode.target,
      targetStart: vnode.targetStart,
      targetAnchor: vnode.targetAnchor,
      staticCount: vnode.staticCount,
      shapeFlag: vnode.shapeFlag,
      // if the vnode is cloned with extra props, we can no longer assume its
      // existing patch flag to be reliable and need to add the FULL_PROPS flag.
      // note: preserve flag for fragments since they use the flag for children
      // fast paths only.
      patchFlag: extraProps && vnode.type !== Fragment ? patchFlag === -1 ? 16 : patchFlag | 16 : patchFlag,
      dynamicProps: vnode.dynamicProps,
      dynamicChildren: vnode.dynamicChildren,
      appContext: vnode.appContext,
      dirs: vnode.dirs,
      transition,
      // These should technically only be non-null on mounted VNodes. However,
      // they *should* be copied for kept-alive vnodes. So we just always copy
      // them since them being non-null during a mount doesn't affect the logic as
      // they will simply be overwritten.
      component: vnode.component,
      suspense: vnode.suspense,
      ssContent: vnode.ssContent && cloneVNode(vnode.ssContent),
      ssFallback: vnode.ssFallback && cloneVNode(vnode.ssFallback),
      placeholder: vnode.placeholder,
      el: vnode.el,
      anchor: vnode.anchor,
      ctx: vnode.ctx,
      ce: vnode.ce,
      cacheIndex: vnode.cacheIndex
    };
    if (transition && cloneTransition) {
      setTransitionHooks(
        cloned,
        transition.clone(cloned)
      );
    }
    return cloned;
  }
  function deepCloneVNode(vnode) {
    const cloned = cloneVNode(vnode);
    if (isArray(vnode.children)) {
      cloned.children = vnode.children.map(deepCloneVNode);
    }
    return cloned;
  }
  function createTextVNode(text = " ", flag = 0) {
    return createVNode(Text, null, text, flag);
  }
  function createCommentVNode(text = "", asBlock = false) {
    return asBlock ? (openBlock(), createBlock(Comment, null, text)) : createVNode(Comment, null, text);
  }
  function normalizeVNode(child) {
    if (child == null || typeof child === "boolean") {
      return createVNode(Comment);
    } else if (isArray(child)) {
      return createVNode(
        Fragment,
        null,
        // #3666, avoid reference pollution when reusing vnode
        child.slice()
      );
    } else if (isVNode(child)) {
      return cloneIfMounted(child);
    } else {
      return createVNode(Text, null, String(child));
    }
  }
  function cloneIfMounted(child) {
    return child.el === null && child.patchFlag !== -1 || child.memo ? child : cloneVNode(child);
  }
  function normalizeChildren(vnode, children2) {
    let type = 0;
    const { shapeFlag } = vnode;
    if (children2 == null) {
      children2 = null;
    } else if (isArray(children2)) {
      type = 16;
    } else if (typeof children2 === "object") {
      if (shapeFlag & (1 | 64)) {
        const slot = children2.default;
        if (slot) {
          slot._c && (slot._d = false);
          normalizeChildren(vnode, slot());
          slot._c && (slot._d = true);
        }
        return;
      } else {
        type = 32;
        const slotFlag = children2._;
        if (!slotFlag && !isInternalObject(children2)) {
          children2._ctx = currentRenderingInstance;
        } else if (slotFlag === 3 && currentRenderingInstance) {
          if (currentRenderingInstance.slots._ === 1) {
            children2._ = 1;
          } else {
            children2._ = 2;
            vnode.patchFlag |= 1024;
          }
        }
      }
    } else if (isFunction(children2)) {
      if (shapeFlag & (1 | 64)) {
        normalizeChildren(vnode, { default: children2 });
        return;
      }
      children2 = { default: children2, _ctx: currentRenderingInstance };
      type = 32;
    } else {
      children2 = String(children2);
      if (shapeFlag & 64) {
        type = 16;
        children2 = [createTextVNode(children2)];
      } else {
        type = 8;
      }
    }
    vnode.children = children2;
    vnode.shapeFlag |= type;
  }
  function mergeProps(...args) {
    const ret = {};
    for (let i = 0; i < args.length; i++) {
      const toMerge = args[i];
      for (const key in toMerge) {
        if (key === "class") {
          if (ret.class !== toMerge.class) {
            ret.class = normalizeClass([ret.class, toMerge.class]);
          }
        } else if (key === "style") {
          ret.style = normalizeStyle([ret.style, toMerge.style]);
        } else if (isOn(key)) {
          const existing = ret[key];
          const incoming = toMerge[key];
          if (incoming && existing !== incoming && !(isArray(existing) && existing.includes(incoming))) {
            ret[key] = existing ? [].concat(existing, incoming) : incoming;
          } else if (incoming == null && existing == null && // mergeProps({ 'onUpdate:modelValue': undefined }) should not retain
          // the model listener.
          !isModelListener(key)) {
            ret[key] = incoming;
          }
        } else if (key !== "") {
          ret[key] = toMerge[key];
        }
      }
    }
    return ret;
  }
  function invokeVNodeHook(hook, instance, vnode, prevVNode = null) {
    callWithAsyncErrorHandling(hook, instance, 7, [
      vnode,
      prevVNode
    ]);
  }
  var emptyAppContext = createAppContext();
  var uid = 0;
  function getNextUid() {
    return uid;
  }
  function createComponentInstance(vnode, parent, suspense) {
    const type = vnode.type;
    const appContext = (parent ? parent.appContext : vnode.appContext) || emptyAppContext;
    const instance = {
      uid: uid++,
      vnode,
      type,
      parent,
      appContext,
      root: null,
      // to be immediately set
      next: null,
      subTree: null,
      // will be set synchronously right after creation
      effect: null,
      update: null,
      // will be set synchronously right after creation
      job: null,
      scope: new EffectScope(
        true
        /* detached */
      ),
      render: null,
      proxy: null,
      exposed: null,
      exposeProxy: null,
      withProxy: null,
      provides: parent ? parent.provides : Object.create(appContext.provides),
      ids: parent ? parent.ids : ["", 0, 0],
      accessCache: null,
      renderCache: [],
      // local resolved assets
      components: null,
      directives: null,
      // resolved props and emits options
      propsOptions: normalizePropsOptions(type, appContext),
      emitsOptions: normalizeEmitsOptions(type, appContext),
      // emit
      emit: null,
      // to be set immediately
      emitted: null,
      // props default value
      propsDefaults: EMPTY_OBJ,
      // inheritAttrs
      inheritAttrs: type.inheritAttrs,
      // state
      ctx: EMPTY_OBJ,
      data: EMPTY_OBJ,
      props: EMPTY_OBJ,
      attrs: EMPTY_OBJ,
      slots: EMPTY_OBJ,
      refs: EMPTY_OBJ,
      setupState: EMPTY_OBJ,
      setupContext: null,
      // suspense related
      suspense,
      suspenseId: suspense ? suspense.pendingId : 0,
      asyncDep: null,
      asyncResolved: false,
      // lifecycle hooks
      // not using enums here because it results in computed properties
      isMounted: false,
      isUnmounted: false,
      isDeactivated: false,
      bc: null,
      c: null,
      bm: null,
      m: null,
      bu: null,
      u: null,
      um: null,
      bum: null,
      da: null,
      a: null,
      rtg: null,
      rtc: null,
      ec: null,
      sp: null
    };
    if (true) {
      instance.ctx = createDevRenderContext(instance);
    } else {
      instance.ctx = { _: instance };
    }
    instance.root = parent ? parent.root : instance;
    instance.emit = emit.bind(null, instance);
    if (vnode.ce) {
      vnode.ce(instance);
    }
    return instance;
  }
  var currentInstance = null;
  var getCurrentInstance = () => currentInstance || currentRenderingInstance;
  var internalSetCurrentInstance;
  var setInSSRSetupState;
  {
    const g = getGlobalThis();
    const registerGlobalSetter = (key, setter) => {
      let setters;
      if (!(setters = g[key])) setters = g[key] = [];
      setters.push(setter);
      return (v) => {
        if (setters.length > 1) setters.forEach((set2) => set2(v));
        else setters[0](v);
      };
    };
    internalSetCurrentInstance = registerGlobalSetter(
      `__VUE_INSTANCE_SETTERS__`,
      (v) => currentInstance = v
    );
    setInSSRSetupState = registerGlobalSetter(
      `__VUE_SSR_SETTERS__`,
      (v) => isInSSRComponentSetup = v
    );
  }
  var setCurrentInstance = (instance) => {
    const prev = currentInstance;
    internalSetCurrentInstance(instance);
    instance.scope.on();
    return () => {
      instance.scope.off();
      internalSetCurrentInstance(prev);
    };
  };
  var unsetCurrentInstance = () => {
    currentInstance && currentInstance.scope.off();
    internalSetCurrentInstance(null);
  };
  var isBuiltInTag = /* @__PURE__ */ makeMap("slot,component");
  function validateComponentName(name, { isNativeTag }) {
    if (isBuiltInTag(name) || isNativeTag(name)) {
      warn$1(
        "Do not use built-in or reserved HTML elements as component id: " + name
      );
    }
  }
  function isStatefulComponent(instance) {
    return instance.vnode.shapeFlag & 4;
  }
  var isInSSRComponentSetup = false;
  function setupComponent(instance, isSSR = false, optimized = false) {
    isSSR && setInSSRSetupState(isSSR);
    const { props, children: children2 } = instance.vnode;
    const isStateful = isStatefulComponent(instance);
    initProps(instance, props, isStateful, isSSR);
    initSlots(instance, children2, optimized || isSSR);
    const setupResult = isStateful ? setupStatefulComponent(instance, isSSR) : void 0;
    isSSR && setInSSRSetupState(false);
    return setupResult;
  }
  function setupStatefulComponent(instance, isSSR) {
    const Component = instance.type;
    if (true) {
      if (Component.name) {
        validateComponentName(Component.name, instance.appContext.config);
      }
      if (Component.components) {
        const names = Object.keys(Component.components);
        for (let i = 0; i < names.length; i++) {
          validateComponentName(names[i], instance.appContext.config);
        }
      }
      if (Component.directives) {
        const names = Object.keys(Component.directives);
        for (let i = 0; i < names.length; i++) {
          validateDirectiveName(names[i]);
        }
      }
      if (Component.compilerOptions && isRuntimeOnly()) {
        warn$1(
          `"compilerOptions" is only supported when using a build of Vue that includes the runtime compiler. Since you are using a runtime-only build, the options should be passed via your build tool config instead.`
        );
      }
    }
    instance.accessCache = /* @__PURE__ */ Object.create(null);
    instance.proxy = new Proxy(instance.ctx, PublicInstanceProxyHandlers);
    if (true) {
      exposePropsOnRenderContext(instance);
    }
    const { setup } = Component;
    if (setup) {
      pauseTracking();
      const setupContext = instance.setupContext = setup.length > 1 ? createSetupContext(instance) : null;
      const reset = setCurrentInstance(instance);
      const setupResult = callWithErrorHandling(
        setup,
        instance,
        0,
        [
          true ? shallowReadonly(instance.props) : instance.props,
          setupContext
        ]
      );
      const isAsyncSetup = isPromise(setupResult);
      resetTracking();
      reset();
      if ((isAsyncSetup || instance.sp) && !isAsyncWrapper(instance)) {
        markAsyncBoundary(instance);
      }
      if (isAsyncSetup) {
        setupResult.then(unsetCurrentInstance, unsetCurrentInstance);
        if (isSSR) {
          return setupResult.then((resolvedResult) => {
            setInSSRSetupState(true);
            try {
              handleSetupResult(instance, resolvedResult, isSSR);
            } finally {
              setInSSRSetupState(false);
            }
          }).catch((e) => {
            handleError(e, instance, 0);
          });
        } else {
          instance.asyncDep = setupResult;
          if (!instance.suspense) {
            const name = formatComponentName(instance, Component);
            warn$1(
              `Component <${name}>: setup function returned a promise, but no <Suspense> boundary was found in the parent component tree. A component with async setup() must be nested in a <Suspense> in order to be rendered.`
            );
          }
        }
      } else {
        handleSetupResult(instance, setupResult, isSSR);
      }
    } else {
      finishComponentSetup(instance, isSSR);
    }
  }
  function handleSetupResult(instance, setupResult, isSSR) {
    if (isFunction(setupResult)) {
      if (instance.type.__ssrInlineRender) {
        instance.ssrRender = setupResult;
      } else {
        instance.render = setupResult;
      }
    } else if (isObject(setupResult)) {
      if (isVNode(setupResult)) {
        warn$1(
          `setup() should not return VNodes directly - return a render function instead.`
        );
      }
      if (true) {
        instance.devtoolsRawSetupState = setupResult;
      }
      instance.setupState = proxyRefs(setupResult);
      if (true) {
        exposeSetupStateOnRenderContext(instance);
      }
    } else if (setupResult !== void 0) {
      warn$1(
        `setup() should return an object. Received: ${setupResult === null ? "null" : typeof setupResult}`
      );
    }
    finishComponentSetup(instance, isSSR);
  }
  var compile;
  var installWithProxy;
  var isRuntimeOnly = () => !compile;
  function finishComponentSetup(instance, isSSR, skipOptions) {
    const Component = instance.type;
    if (!instance.render) {
      if (!isSSR && compile && !Component.render) {
        const template = Component.template || resolveMergedOptions(instance).template;
        if (template) {
          if (true) {
            startMeasure(instance, `compile`);
          }
          const { isCustomElement, compilerOptions } = instance.appContext.config;
          const { delimiters, compilerOptions: componentCompilerOptions } = Component;
          const finalCompilerOptions = extend(
            extend(
              {
                isCustomElement,
                delimiters
              },
              compilerOptions
            ),
            componentCompilerOptions
          );
          Component.render = compile(template, finalCompilerOptions);
          if (true) {
            endMeasure(instance, `compile`);
          }
        }
      }
      instance.render = Component.render || NOOP;
      if (installWithProxy) {
        installWithProxy(instance);
      }
    }
    if (true) {
      const reset = setCurrentInstance(instance);
      pauseTracking();
      try {
        applyOptions(instance);
      } finally {
        resetTracking();
        reset();
      }
    }
    if (!Component.render && instance.render === NOOP && !isSSR) {
      if (!compile && Component.template) {
        warn$1(
          `Component provided template option but runtime compilation is not supported in this build of Vue. Configure your bundler to alias "vue" to "vue/dist/vue.esm-bundler.js".`
        );
      } else {
        warn$1(`Component is missing template or render function: `, Component);
      }
    }
  }
  var attrsProxyHandlers = true ? {
    get(target, key) {
      markAttrsAccessed();
      track(target, "get", "");
      return target[key];
    },
    set() {
      warn$1(`setupContext.attrs is readonly.`);
      return false;
    },
    deleteProperty() {
      warn$1(`setupContext.attrs is readonly.`);
      return false;
    }
  } : {
    get(target, key) {
      track(target, "get", "");
      return target[key];
    }
  };
  function getSlotsProxy(instance) {
    return new Proxy(instance.slots, {
      get(target, key) {
        track(instance, "get", "$slots");
        return target[key];
      }
    });
  }
  function createSetupContext(instance) {
    const expose = (exposed) => {
      if (true) {
        if (instance.exposed) {
          warn$1(`expose() should be called only once per setup().`);
        }
        if (exposed != null) {
          let exposedType = typeof exposed;
          if (exposedType === "object") {
            if (isArray(exposed)) {
              exposedType = "array";
            } else if (isRef2(exposed)) {
              exposedType = "ref";
            }
          }
          if (exposedType !== "object") {
            warn$1(
              `expose() should be passed a plain object, received ${exposedType}.`
            );
          }
        }
      }
      instance.exposed = exposed || {};
    };
    if (true) {
      let attrsProxy;
      let slotsProxy;
      return Object.freeze({
        get attrs() {
          return attrsProxy || (attrsProxy = new Proxy(instance.attrs, attrsProxyHandlers));
        },
        get slots() {
          return slotsProxy || (slotsProxy = getSlotsProxy(instance));
        },
        get emit() {
          return (event, ...args) => instance.emit(event, ...args);
        },
        expose
      });
    } else {
      return {
        attrs: new Proxy(instance.attrs, attrsProxyHandlers),
        slots: instance.slots,
        emit: instance.emit,
        expose
      };
    }
  }
  function getComponentPublicInstance(instance) {
    if (instance.exposed) {
      return instance.exposeProxy || (instance.exposeProxy = new Proxy(proxyRefs(markRaw(instance.exposed)), {
        get(target, key) {
          if (key in target) {
            return target[key];
          } else if (key in publicPropertiesMap) {
            return publicPropertiesMap[key](instance);
          }
        },
        has(target, key) {
          return key in target || key in publicPropertiesMap;
        }
      }));
    } else {
      return instance.proxy;
    }
  }
  var classifyRE = /(?:^|[-_])\w/g;
  var classify = (str) => str.replace(classifyRE, (c) => c.toUpperCase()).replace(/[-_]/g, "");
  function getComponentName(Component, includeInferred = true) {
    return isFunction(Component) ? Component.displayName || Component.name : Component.name || includeInferred && Component.__name;
  }
  function formatComponentName(instance, Component, isRoot = false) {
    let name = getComponentName(Component);
    if (!name && Component.__file) {
      const match = Component.__file.match(/([^/\\]+)\.\w+$/);
      if (match) {
        name = match[1];
      }
    }
    if (!name && instance) {
      const inferFromRegistry = (registry) => {
        for (const key in registry) {
          if (registry[key] === Component) {
            return key;
          }
        }
      };
      name = inferFromRegistry(instance.components) || instance.parent && inferFromRegistry(
        instance.parent.type.components
      ) || inferFromRegistry(instance.appContext.components);
    }
    return name ? classify(name) : isRoot ? `App` : `Anonymous`;
  }
  function isClassComponent(value) {
    return isFunction(value) && "__vccOpts" in value;
  }
  var computed2 = (getterOrOptions, debugOptions) => {
    const c = computed(getterOrOptions, debugOptions, isInSSRComponentSetup);
    if (true) {
      const i = getCurrentInstance();
      if (i && i.appContext.config.warnRecursiveComputed) {
        c._warnRecursive = true;
      }
    }
    return c;
  };
  function h(type, propsOrChildren, children2) {
    try {
      setBlockTracking(-1);
      const l = arguments.length;
      if (l === 2) {
        if (isObject(propsOrChildren) && !isArray(propsOrChildren)) {
          if (isVNode(propsOrChildren)) {
            return createVNode(type, null, [propsOrChildren]);
          }
          return createVNode(type, propsOrChildren);
        } else {
          return createVNode(type, null, propsOrChildren);
        }
      } else {
        if (l > 3) {
          children2 = Array.prototype.slice.call(arguments, 2);
        } else if (l === 3 && isVNode(children2)) {
          children2 = [children2];
        }
        return createVNode(type, propsOrChildren, children2);
      }
    } finally {
      setBlockTracking(1);
    }
  }
  function initCustomFormatter() {
    if (typeof window === "undefined") {
      return;
    }
    const vueStyle = { style: "color:#3ba776" };
    const numberStyle = { style: "color:#1677ff" };
    const stringStyle = { style: "color:#f5222d" };
    const keywordStyle = { style: "color:#eb2f96" };
    const formatter = {
      __vue_custom_formatter: true,
      header(obj) {
        if (!isObject(obj)) {
          return null;
        }
        if (obj.__isVue) {
          return ["div", vueStyle, `VueInstance`];
        } else if (isRef2(obj)) {
          pauseTracking();
          const value = obj.value;
          resetTracking();
          return [
            "div",
            {},
            ["span", vueStyle, genRefFlag(obj)],
            "<",
            formatValue(value),
            `>`
          ];
        } else if (isReactive(obj)) {
          return [
            "div",
            {},
            ["span", vueStyle, isShallow(obj) ? "ShallowReactive" : "Reactive"],
            "<",
            formatValue(obj),
            `>${isReadonly(obj) ? ` (readonly)` : ``}`
          ];
        } else if (isReadonly(obj)) {
          return [
            "div",
            {},
            ["span", vueStyle, isShallow(obj) ? "ShallowReadonly" : "Readonly"],
            "<",
            formatValue(obj),
            ">"
          ];
        }
        return null;
      },
      hasBody(obj) {
        return obj && obj.__isVue;
      },
      body(obj) {
        if (obj && obj.__isVue) {
          return [
            "div",
            {},
            ...formatInstance(obj.$)
          ];
        }
      }
    };
    function formatInstance(instance) {
      const blocks = [];
      if (instance.type.props && instance.props) {
        blocks.push(createInstanceBlock("props", toRaw(instance.props)));
      }
      if (instance.setupState !== EMPTY_OBJ) {
        blocks.push(createInstanceBlock("setup", instance.setupState));
      }
      if (instance.data !== EMPTY_OBJ) {
        blocks.push(createInstanceBlock("data", toRaw(instance.data)));
      }
      const computed3 = extractKeys(instance, "computed");
      if (computed3) {
        blocks.push(createInstanceBlock("computed", computed3));
      }
      const injected = extractKeys(instance, "inject");
      if (injected) {
        blocks.push(createInstanceBlock("injected", injected));
      }
      blocks.push([
        "div",
        {},
        [
          "span",
          {
            style: keywordStyle.style + ";opacity:0.66"
          },
          "$ (internal): "
        ],
        ["object", { object: instance }]
      ]);
      return blocks;
    }
    function createInstanceBlock(type, target) {
      target = extend({}, target);
      if (!Object.keys(target).length) {
        return ["span", {}];
      }
      return [
        "div",
        { style: "line-height:1.25em;margin-bottom:0.6em" },
        [
          "div",
          {
            style: "color:#476582"
          },
          type
        ],
        [
          "div",
          {
            style: "padding-left:1.25em"
          },
          ...Object.keys(target).map((key) => {
            return [
              "div",
              {},
              ["span", keywordStyle, key + ": "],
              formatValue(target[key], false)
            ];
          })
        ]
      ];
    }
    function formatValue(v, asRaw = true) {
      if (typeof v === "number") {
        return ["span", numberStyle, v];
      } else if (typeof v === "string") {
        return ["span", stringStyle, JSON.stringify(v)];
      } else if (typeof v === "boolean") {
        return ["span", keywordStyle, v];
      } else if (isObject(v)) {
        return ["object", { object: asRaw ? toRaw(v) : v }];
      } else {
        return ["span", stringStyle, String(v)];
      }
    }
    function extractKeys(instance, type) {
      const Comp = instance.type;
      if (isFunction(Comp)) {
        return;
      }
      const extracted = {};
      for (const key in instance.ctx) {
        if (isKeyOfType(Comp, key, type)) {
          extracted[key] = instance.ctx[key];
        }
      }
      return extracted;
    }
    function isKeyOfType(Comp, key, type) {
      const opts = Comp[type];
      if (isArray(opts) && opts.includes(key) || isObject(opts) && key in opts) {
        return true;
      }
      if (Comp.extends && isKeyOfType(Comp.extends, key, type)) {
        return true;
      }
      if (Comp.mixins && Comp.mixins.some((m) => isKeyOfType(m, key, type))) {
        return true;
      }
    }
    function genRefFlag(v) {
      if (isShallow(v)) {
        return `ShallowRef`;
      }
      if (v.effect) {
        return `ComputedRef`;
      }
      return `Ref`;
    }
    if (window.devtoolsFormatters) {
      window.devtoolsFormatters.push(formatter);
    } else {
      window.devtoolsFormatters = [formatter];
    }
  }
  function isMemoSame(cached, memo) {
    const prev = cached.memo;
    if (prev.length != memo.length) {
      return false;
    }
    for (let i = 0; i < prev.length; i++) {
      if (hasChanged(prev[i], memo[i])) {
        return false;
      }
    }
    if (isBlockTreeEnabled > 0 && currentBlock) {
      currentBlock.push(cached);
    }
    return true;
  }
  var version = "3.5.44";
  var warn2 = true ? warn$1 : NOOP;

  // node_modules/@vue/runtime-dom/dist/runtime-dom.esm-bundler.js
  var policy = void 0;
  var tt = typeof window !== "undefined" && window.trustedTypes;
  if (tt) {
    try {
      policy = /* @__PURE__ */ tt.createPolicy("vue", {
        createHTML: (val) => val
      });
    } catch (e) {
      warn2(`Error creating trusted types policy: ${e}`);
    }
  }
  var unsafeToTrustedHTML = policy ? (val) => policy.createHTML(val) : (val) => val;
  var svgNS = "http://www.w3.org/2000/svg";
  var mathmlNS = "http://www.w3.org/1998/Math/MathML";
  var doc = typeof document !== "undefined" ? document : null;
  var templateContainer = doc && /* @__PURE__ */ doc.createElement("template");
  var nodeOps = {
    insert: (child, parent, anchor) => {
      parent.insertBefore(child, anchor || null);
    },
    remove: (child) => {
      const parent = child.parentNode;
      if (parent) {
        parent.removeChild(child);
      }
    },
    createElement: (tag, namespace2, is, props) => {
      const el = namespace2 === "svg" ? doc.createElementNS(svgNS, tag) : namespace2 === "mathml" ? doc.createElementNS(mathmlNS, tag) : is ? doc.createElement(tag, { is }) : doc.createElement(tag);
      if (tag === "select" && props && props.multiple != null) {
        el.setAttribute("multiple", props.multiple);
      }
      return el;
    },
    createText: (text) => doc.createTextNode(text),
    createComment: (text) => doc.createComment(text),
    setText: (node, text) => {
      node.nodeValue = text;
    },
    setElementText: (el, text) => {
      el.textContent = text;
    },
    parentNode: (node) => node.parentNode,
    nextSibling: (node) => node.nextSibling,
    querySelector: (selector2) => doc.querySelector(selector2),
    setScopeId(el, id2) {
      el.setAttribute(id2, "");
    },
    // __UNSAFE__
    // Reason: innerHTML.
    // Static content here can only come from compiled templates.
    // As long as the user only uses trusted templates, this is safe.
    insertStaticContent(content, parent, anchor, namespace2, start2, end) {
      const before = anchor ? anchor.previousSibling : parent.lastChild;
      if (start2 && (start2 === end || start2.nextSibling)) {
        while (true) {
          parent.insertBefore(start2.cloneNode(true), anchor);
          if (start2 === end || !(start2 = start2.nextSibling)) break;
        }
      } else {
        templateContainer.innerHTML = unsafeToTrustedHTML(
          namespace2 === "svg" ? `<svg>${content}</svg>` : namespace2 === "mathml" ? `<math>${content}</math>` : content
        );
        const template = templateContainer.content;
        if (namespace2 === "svg" || namespace2 === "mathml") {
          const wrapper = template.firstChild;
          while (wrapper.firstChild) {
            template.appendChild(wrapper.firstChild);
          }
          template.removeChild(wrapper);
        }
        parent.insertBefore(template, anchor);
      }
      return [
        // first
        before ? before.nextSibling : parent.firstChild,
        // last
        anchor ? anchor.previousSibling : parent.lastChild
      ];
    }
  };
  var vtcKey = /* @__PURE__ */ Symbol("_vtc");
  function patchClass(el, value, isSVG) {
    const transitionClasses = el[vtcKey];
    if (transitionClasses) {
      value = (value ? [value, ...transitionClasses] : [...transitionClasses]).join(" ");
    }
    if (value == null) {
      el.removeAttribute("class");
    } else if (isSVG) {
      el.setAttribute("class", value);
    } else {
      el.className = value;
    }
  }
  var vShowOriginalDisplay = /* @__PURE__ */ Symbol("_vod");
  var vShowHidden = /* @__PURE__ */ Symbol("_vsh");
  var CSS_VAR_TEXT = /* @__PURE__ */ Symbol(true ? "CSS_VAR_TEXT" : "");
  var displayRE = /(?:^|;)\s*display\s*:/;
  function patchStyle(el, prev, next) {
    const style = el.style;
    const isCssString = isString(next);
    let hasControlledDisplay = false;
    if (next && !isCssString) {
      if (prev) {
        if (!isString(prev)) {
          for (const key in prev) {
            if (next[key] == null) {
              setStyle(style, key, "");
            }
          }
        } else {
          for (const prevStyle of prev.split(";")) {
            const key = prevStyle.slice(0, prevStyle.indexOf(":")).trim();
            if (next[key] == null) {
              setStyle(style, key, "");
            }
          }
        }
      }
      for (const key in next) {
        if (key === "display") {
          hasControlledDisplay = true;
        }
        const value = next[key];
        if (value != null) {
          if (!shouldPreserveTextareaResizeStyle(
            el,
            key,
            !isString(prev) && prev ? prev[key] : void 0,
            value
          )) {
            setStyle(style, key, value);
          }
        } else {
          setStyle(style, key, "");
        }
      }
    } else {
      if (isCssString) {
        if (prev !== next) {
          const cssVarText = style[CSS_VAR_TEXT];
          if (cssVarText) {
            next += ";" + cssVarText;
          }
          style.cssText = next;
          hasControlledDisplay = displayRE.test(next);
        }
      } else if (prev) {
        el.removeAttribute("style");
      }
    }
    if (vShowOriginalDisplay in el) {
      if (!isCssString || prev !== next) {
        el[vShowOriginalDisplay] = hasControlledDisplay ? style.display : "";
      }
      if (el[vShowHidden]) {
        style.display = "none";
      }
    }
  }
  var semicolonRE = /[^\\];\s*$/;
  var importantRE = /\s*!important$/;
  function setStyle(style, name, val) {
    if (isArray(val)) {
      val.forEach((v) => setStyle(style, name, v));
    } else {
      if (val == null) val = "";
      if (true) {
        if (semicolonRE.test(val)) {
          warn2(
            `Unexpected semicolon at the end of '${name}' style value: '${val}'`
          );
        }
      }
      if (name.startsWith("--")) {
        if (importantRE.test(val)) {
          style.setProperty(name, val.replace(importantRE, ""), "important");
        } else {
          style.setProperty(name, val);
        }
      } else {
        const prefixed = autoPrefix(style, name);
        if (importantRE.test(val)) {
          style.setProperty(
            hyphenate(prefixed),
            val.replace(importantRE, ""),
            "important"
          );
        } else {
          style[prefixed] = val;
        }
      }
    }
  }
  var prefixes = ["Webkit", "Moz", "ms"];
  var prefixCache = {};
  function autoPrefix(style, rawName) {
    const cached = prefixCache[rawName];
    if (cached) {
      return cached;
    }
    let name = camelize(rawName);
    if (name !== "filter" && name in style) {
      return prefixCache[rawName] = name;
    }
    name = capitalize(name);
    for (let i = 0; i < prefixes.length; i++) {
      const prefixed = prefixes[i] + name;
      if (prefixed in style) {
        return prefixCache[rawName] = prefixed;
      }
    }
    return rawName;
  }
  function shouldPreserveTextareaResizeStyle(el, key, prev, next) {
    return el.tagName === "TEXTAREA" && (key === "width" || key === "height") && isString(next) && prev === next;
  }
  var xlinkNS = "http://www.w3.org/1999/xlink";
  function patchAttr(el, key, value, isSVG, instance, isBoolean = isSpecialBooleanAttr(key)) {
    if (isSVG && key.startsWith("xlink:")) {
      if (value == null) {
        el.removeAttributeNS(xlinkNS, key.slice(6, key.length));
      } else {
        el.setAttributeNS(xlinkNS, key, value);
      }
    } else {
      if (value == null || isBoolean && !includeBooleanAttr(value)) {
        el.removeAttribute(key);
      } else {
        el.setAttribute(
          key,
          isBoolean ? "" : isSymbol(value) ? String(value) : value
        );
      }
    }
  }
  function patchDOMProp(el, key, value, parentComponent, attrName) {
    if (key === "innerHTML" || key === "textContent") {
      if (value != null) {
        el[key] = key === "innerHTML" ? unsafeToTrustedHTML(value) : value;
      }
      return;
    }
    const tag = el.tagName;
    if (key === "value" && tag !== "PROGRESS" && // custom elements may use _value internally
    !tag.includes("-")) {
      const oldValue = tag === "OPTION" ? el.getAttribute("value") || "" : el.value;
      const newValue = value == null ? (
        // #11647: value should be set as empty string for null and undefined,
        // but <input type="checkbox"> should be set as 'on'.
        el.type === "checkbox" ? "on" : ""
      ) : String(value);
      if (oldValue !== newValue || !("_value" in el)) {
        el.value = newValue;
      }
      if (value == null) {
        el.removeAttribute(key);
      }
      el._value = value;
      return;
    }
    let needRemove = false;
    if (value === "" || value == null) {
      const type = typeof el[key];
      if (type === "boolean") {
        value = includeBooleanAttr(value);
      } else if (value == null && type === "string") {
        value = "";
        needRemove = true;
      } else if (type === "number") {
        value = 0;
        needRemove = true;
      }
    }
    try {
      el[key] = value;
    } catch (e) {
      if (!needRemove) {
        warn2(
          `Failed setting prop "${key}" on <${tag.toLowerCase()}>: value ${value} is invalid.`,
          e
        );
      }
    }
    needRemove && el.removeAttribute(attrName || key);
  }
  function addEventListener(el, event, handler, options) {
    el.addEventListener(event, handler, options);
  }
  function removeEventListener(el, event, handler, options) {
    el.removeEventListener(event, handler, options);
  }
  var veiKey = /* @__PURE__ */ Symbol("_vei");
  function patchEvent(el, rawName, prevValue, nextValue, instance = null) {
    const invokers = el[veiKey] || (el[veiKey] = {});
    const existingInvoker = invokers[rawName];
    if (nextValue && existingInvoker) {
      existingInvoker.value = true ? sanitizeEventValue(nextValue, rawName) : nextValue;
    } else {
      const [name, options] = parseName(rawName);
      if (nextValue) {
        const invoker = invokers[rawName] = createInvoker(
          true ? sanitizeEventValue(nextValue, rawName) : nextValue,
          instance
        );
        addEventListener(el, name, invoker, options);
      } else if (existingInvoker) {
        removeEventListener(el, name, existingInvoker, options);
        invokers[rawName] = void 0;
      }
    }
  }
  var optionsModifierRE = /(Once|Passive|Capture)$/;
  var optionsModifierEventRE = /^on:?(?:Once|Passive|Capture)$/;
  function parseName(name) {
    let options;
    let m;
    while ((m = name.match(optionsModifierRE)) && !optionsModifierEventRE.test(name)) {
      if (!options) options = {};
      name = name.slice(0, name.length - m[1].length);
      options[m[1].toLowerCase()] = true;
    }
    const event = name[2] === ":" ? name.slice(3) : hyphenate(name.slice(2));
    return [event, options];
  }
  var cachedNow = 0;
  var p = /* @__PURE__ */ Promise.resolve();
  var getNow = () => cachedNow || (p.then(() => cachedNow = 0), cachedNow = Date.now());
  function createInvoker(initialValue, instance) {
    const invoker = (e) => {
      if (!e._vts) {
        e._vts = Date.now();
      } else if (e._vts <= invoker.attached) {
        return;
      }
      const value = invoker.value;
      if (isArray(value)) {
        const originalStop = e.stopImmediatePropagation;
        e.stopImmediatePropagation = () => {
          originalStop.call(e);
          e._stopped = true;
        };
        const handlers = value.slice();
        const args = [e];
        for (let i = 0; i < handlers.length; i++) {
          if (e._stopped) {
            break;
          }
          const handler = handlers[i];
          if (handler) {
            callWithAsyncErrorHandling(
              handler,
              instance,
              5,
              args
            );
          }
        }
      } else {
        callWithAsyncErrorHandling(
          value,
          instance,
          5,
          [e]
        );
      }
    };
    invoker.value = initialValue;
    invoker.attached = getNow();
    return invoker;
  }
  function sanitizeEventValue(value, propName) {
    if (isFunction(value) || isArray(value)) {
      return value;
    }
    warn2(
      `Wrong type passed as event handler to ${propName} - did you forget @ or : in front of your prop?
Expected function or array of functions, received type ${typeof value}.`
    );
    return NOOP;
  }
  var isNativeOn = (key) => key.charCodeAt(0) === 111 && key.charCodeAt(1) === 110 && // lowercase letter
  key.charCodeAt(2) > 96 && key.charCodeAt(2) < 123;
  var patchProp = (el, key, prevValue, nextValue, namespace2, parentComponent) => {
    const isSVG = namespace2 === "svg";
    if (key === "class") {
      patchClass(el, nextValue, isSVG);
    } else if (key === "style") {
      patchStyle(el, prevValue, nextValue);
    } else if (isOn(key)) {
      if (!isModelListener(key)) {
        patchEvent(el, key, prevValue, nextValue, parentComponent);
      }
    } else if (key[0] === "." ? (key = key.slice(1), true) : key[0] === "^" ? (key = key.slice(1), false) : shouldSetAsProp(el, key, nextValue, isSVG)) {
      patchDOMProp(el, key, nextValue);
      if (!el.tagName.includes("-") && (key === "value" || key === "checked" || key === "selected")) {
        patchAttr(el, key, nextValue, isSVG, parentComponent, key !== "value");
      }
    } else if (
      // #11081 force set props for possible async custom element
      el._isVueCE && // #12408 check if it's declared prop or it's async custom element
      (shouldSetAsPropForVueCE(el, key) || // @ts-expect-error _def is private
      el._def.__asyncLoader && (/[A-Z]/.test(key) || !isString(nextValue)))
    ) {
      patchDOMProp(el, camelize(key), nextValue, parentComponent, key);
    } else {
      if (key === "true-value") {
        el._trueValue = nextValue;
      } else if (key === "false-value") {
        el._falseValue = nextValue;
      }
      patchAttr(el, key, nextValue, isSVG);
    }
  };
  function shouldSetAsProp(el, key, value, isSVG) {
    if (isSVG) {
      if (key === "innerHTML" || key === "textContent") {
        return true;
      }
      if (key in el && isNativeOn(key) && isFunction(value)) {
        return true;
      }
      return false;
    }
    if (key === "spellcheck" || key === "draggable" || key === "translate" || key === "autocorrect") {
      return false;
    }
    if (key === "sandbox" && el.tagName === "IFRAME") {
      return false;
    }
    if (key === "form") {
      return false;
    }
    if (key === "list" && el.tagName === "INPUT") {
      return false;
    }
    if (key === "type" && el.tagName === "TEXTAREA") {
      return false;
    }
    if (key === "width" || key === "height") {
      const tag = el.tagName;
      if (tag === "IMG" || tag === "VIDEO" || tag === "CANVAS" || tag === "SOURCE") {
        return false;
      }
    }
    if (isNativeOn(key) && isString(value)) {
      return false;
    }
    return key in el;
  }
  function shouldSetAsPropForVueCE(el, key) {
    const props = (
      // @ts-expect-error _def is private
      el._def.props
    );
    if (!props) {
      return false;
    }
    const camelKey = camelize(key);
    return Array.isArray(props) ? props.some((prop) => camelize(prop) === camelKey) : Object.keys(props).some((prop) => camelize(prop) === camelKey);
  }
  var rendererOptions = /* @__PURE__ */ extend({ patchProp }, nodeOps);
  var renderer;
  function ensureRenderer() {
    return renderer || (renderer = createRenderer(rendererOptions));
  }
  var createApp = ((...args) => {
    const app = ensureRenderer().createApp(...args);
    if (true) {
      injectNativeTagCheck(app);
      injectCompilerOptionsCheck(app);
    }
    const { mount: mount2 } = app;
    app.mount = (containerOrSelector) => {
      const container = normalizeContainer(containerOrSelector);
      if (!container) return;
      const component = app._component;
      if (!isFunction(component) && !component.render && !component.template) {
        component.template = container.innerHTML;
      }
      if (container.nodeType === 1) {
        container.textContent = "";
      }
      const proxy = mount2(container, false, resolveRootNamespace(container));
      if (container instanceof Element) {
        container.removeAttribute("v-cloak");
        container.setAttribute("data-v-app", "");
      }
      return proxy;
    };
    return app;
  });
  function resolveRootNamespace(container) {
    if (container instanceof SVGElement) {
      return "svg";
    }
    if (typeof MathMLElement === "function" && container instanceof MathMLElement) {
      return "mathml";
    }
  }
  function injectNativeTagCheck(app) {
    Object.defineProperty(app.config, "isNativeTag", {
      value: (tag) => isHTMLTag(tag) || isSVGTag(tag) || isMathMLTag(tag),
      writable: false
    });
  }
  function injectCompilerOptionsCheck(app) {
    if (isRuntimeOnly()) {
      const isCustomElement = app.config.isCustomElement;
      Object.defineProperty(app.config, "isCustomElement", {
        get() {
          return isCustomElement;
        },
        set() {
          warn2(
            `The \`isCustomElement\` config option is deprecated. Use \`compilerOptions.isCustomElement\` instead.`
          );
        }
      });
      const compilerOptions = app.config.compilerOptions;
      const msg = `The \`compilerOptions\` config option is only respected when using a build of Vue.js that includes the runtime compiler (aka "full build"). Since you are using the runtime-only build, \`compilerOptions\` must be passed to \`@vue/compiler-dom\` in the build setup instead.
- For vue-loader: pass it via vue-loader's \`compilerOptions\` loader option.
- For vue-cli: see https://cli.vuejs.org/guide/webpack.html#modifying-options-of-a-loader
- For vite: pass it via @vitejs/plugin-vue options. See https://github.com/vitejs/vite-plugin-vue/tree/main/packages/plugin-vue#example-for-passing-options-to-vuecompiler-sfc`;
      Object.defineProperty(app.config, "compilerOptions", {
        get() {
          warn2(msg);
          return compilerOptions;
        },
        set() {
          warn2(msg);
        }
      });
    }
  }
  function normalizeContainer(container) {
    if (isString(container)) {
      const res = document.querySelector(container);
      if (!res) {
        warn2(
          `Failed to mount app: mount target selector "${container}" returned null.`
        );
      }
      return res;
    }
    if (window.ShadowRoot && container instanceof window.ShadowRoot && container.mode === "closed") {
      warn2(
        `mounting on a ShadowRoot with \`{mode: "closed"}\` may lead to unpredictable bugs`
      );
    }
    return container;
  }

  // node_modules/vue/dist/vue.runtime.esm-bundler.js
  function initDev() {
    {
      initCustomFormatter();
    }
  }
  if (true) {
    initDev();
  }

  // node_modules/@vue-flow/core/dist/vue-flow-core.mjs
  function tryOnScopeDispose(fn) {
    if (getCurrentScope()) {
      onScopeDispose(fn);
      return true;
    }
    return false;
  }
  function toValue2(r) {
    return typeof r === "function" ? r() : unref(r);
  }
  var isClient = typeof window !== "undefined" && typeof document !== "undefined";
  var isDef$1 = (val) => typeof val !== "undefined";
  var toString = Object.prototype.toString;
  var isObject2 = (val) => toString.call(val) === "[object Object]";
  var noop$3 = () => {
  };
  function createFilterWrapper(filter2, fn) {
    function wrapper(...args) {
      return new Promise((resolve2, reject) => {
        Promise.resolve(filter2(() => fn.apply(this, args), { fn, thisArg: this, args })).then(resolve2).catch(reject);
      });
    }
    return wrapper;
  }
  var bypassFilter = (invoke) => {
    return invoke();
  };
  function pausableFilter(extendFilter = bypassFilter) {
    const isActive = ref(true);
    function pause() {
      isActive.value = false;
    }
    function resume() {
      isActive.value = true;
    }
    const eventFilter = (...args) => {
      if (isActive.value)
        extendFilter(...args);
    };
    return { isActive: readonly(isActive), pause, resume, eventFilter };
  }
  function promiseTimeout(ms, throwOnTimeout = false, reason = "Timeout") {
    return new Promise((resolve2, reject) => {
      if (throwOnTimeout)
        setTimeout(() => reject(reason), ms);
      else
        setTimeout(resolve2, ms);
    });
  }
  function watchWithFilter(source, cb, options = {}) {
    const {
      eventFilter = bypassFilter,
      ...watchOptions
    } = options;
    return watch2(
      source,
      createFilterWrapper(
        eventFilter,
        cb
      ),
      watchOptions
    );
  }
  function watchPausable(source, cb, options = {}) {
    const {
      eventFilter: filter2,
      ...watchOptions
    } = options;
    const { eventFilter, pause, resume, isActive } = pausableFilter(filter2);
    const stop2 = watchWithFilter(
      source,
      cb,
      {
        ...watchOptions,
        eventFilter
      }
    );
    return { stop: stop2, pause, resume, isActive };
  }
  function toRefs2(objectRef, options = {}) {
    if (!isRef2(objectRef))
      return toRefs(objectRef);
    const result = Array.isArray(objectRef.value) ? Array.from({ length: objectRef.value.length }) : {};
    for (const key in objectRef.value) {
      result[key] = customRef(() => ({
        get() {
          return objectRef.value[key];
        },
        set(v) {
          var _a;
          const replaceRef = (_a = toValue2(options.replaceRef)) != null ? _a : true;
          if (replaceRef) {
            if (Array.isArray(objectRef.value)) {
              const copy = [...objectRef.value];
              copy[key] = v;
              objectRef.value = copy;
            } else {
              const newObject = { ...objectRef.value, [key]: v };
              Object.setPrototypeOf(newObject, Object.getPrototypeOf(objectRef.value));
              objectRef.value = newObject;
            }
          } else {
            objectRef.value[key] = v;
          }
        }
      }));
    }
    return result;
  }
  function createUntil(r, isNot = false) {
    function toMatch(condition, { flush = "sync", deep = false, timeout: timeout2, throwOnTimeout } = {}) {
      let stop2 = null;
      const watcher = new Promise((resolve2) => {
        stop2 = watch2(
          r,
          (v) => {
            if (condition(v) !== isNot) {
              stop2 == null ? void 0 : stop2();
              resolve2(v);
            }
          },
          {
            flush,
            deep,
            immediate: true
          }
        );
      });
      const promises = [watcher];
      if (timeout2 != null) {
        promises.push(
          promiseTimeout(timeout2, throwOnTimeout).then(() => toValue2(r)).finally(() => stop2 == null ? void 0 : stop2())
        );
      }
      return Promise.race(promises);
    }
    function toBe(value, options) {
      if (!isRef2(value))
        return toMatch((v) => v === value, options);
      const { flush = "sync", deep = false, timeout: timeout2, throwOnTimeout } = options != null ? options : {};
      let stop2 = null;
      const watcher = new Promise((resolve2) => {
        stop2 = watch2(
          [r, value],
          ([v1, v2]) => {
            if (isNot !== (v1 === v2)) {
              stop2 == null ? void 0 : stop2();
              resolve2(v1);
            }
          },
          {
            flush,
            deep,
            immediate: true
          }
        );
      });
      const promises = [watcher];
      if (timeout2 != null) {
        promises.push(
          promiseTimeout(timeout2, throwOnTimeout).then(() => toValue2(r)).finally(() => {
            stop2 == null ? void 0 : stop2();
            return toValue2(r);
          })
        );
      }
      return Promise.race(promises);
    }
    function toBeTruthy(options) {
      return toMatch((v) => Boolean(v), options);
    }
    function toBeNull(options) {
      return toBe(null, options);
    }
    function toBeUndefined(options) {
      return toBe(void 0, options);
    }
    function toBeNaN(options) {
      return toMatch(Number.isNaN, options);
    }
    function toContains(value, options) {
      return toMatch((v) => {
        const array2 = Array.from(v);
        return array2.includes(value) || array2.includes(toValue2(value));
      }, options);
    }
    function changed(options) {
      return changedTimes(1, options);
    }
    function changedTimes(n = 1, options) {
      let count = -1;
      return toMatch(() => {
        count += 1;
        return count >= n;
      }, options);
    }
    if (Array.isArray(toValue2(r))) {
      const instance = {
        toMatch,
        toContains,
        changed,
        changedTimes,
        get not() {
          return createUntil(r, !isNot);
        }
      };
      return instance;
    } else {
      const instance = {
        toMatch,
        toBe,
        toBeTruthy,
        toBeNull,
        toBeNaN,
        toBeUndefined,
        changed,
        changedTimes,
        get not() {
          return createUntil(r, !isNot);
        }
      };
      return instance;
    }
  }
  function until(r) {
    return createUntil(r);
  }
  function unrefElement(elRef) {
    var _a;
    const plain = toValue2(elRef);
    return (_a = plain == null ? void 0 : plain.$el) != null ? _a : plain;
  }
  var defaultWindow = isClient ? window : void 0;
  function useEventListener(...args) {
    let target;
    let events;
    let listeners;
    let options;
    if (typeof args[0] === "string" || Array.isArray(args[0])) {
      [events, listeners, options] = args;
      target = defaultWindow;
    } else {
      [target, events, listeners, options] = args;
    }
    if (!target)
      return noop$3;
    if (!Array.isArray(events))
      events = [events];
    if (!Array.isArray(listeners))
      listeners = [listeners];
    const cleanups = [];
    const cleanup = () => {
      cleanups.forEach((fn) => fn());
      cleanups.length = 0;
    };
    const register = (el, event, listener, options2) => {
      el.addEventListener(event, listener, options2);
      return () => el.removeEventListener(event, listener, options2);
    };
    const stopWatch = watch2(
      () => [unrefElement(target), toValue2(options)],
      ([el, options2]) => {
        cleanup();
        if (!el)
          return;
        const optionsClone = isObject2(options2) ? { ...options2 } : options2;
        cleanups.push(
          ...events.flatMap((event) => {
            return listeners.map((listener) => register(el, event, listener, optionsClone));
          })
        );
      },
      { immediate: true, flush: "post" }
    );
    const stop2 = () => {
      stopWatch();
      cleanup();
    };
    tryOnScopeDispose(stop2);
    return stop2;
  }
  function createKeyPredicate$1(keyFilter) {
    if (typeof keyFilter === "function")
      return keyFilter;
    else if (typeof keyFilter === "string")
      return (event) => event.key === keyFilter;
    else if (Array.isArray(keyFilter))
      return (event) => keyFilter.includes(event.key);
    return () => true;
  }
  function onKeyStroke(...args) {
    let key;
    let handler;
    let options = {};
    if (args.length === 3) {
      key = args[0];
      handler = args[1];
      options = args[2];
    } else if (args.length === 2) {
      if (typeof args[1] === "object") {
        key = true;
        handler = args[0];
        options = args[1];
      } else {
        key = args[0];
        handler = args[1];
      }
    } else {
      key = true;
      handler = args[0];
    }
    const {
      target = defaultWindow,
      eventName = "keydown",
      passive = false,
      dedupe: dedupe2 = false
    } = options;
    const predicate = createKeyPredicate$1(key);
    const listener = (e) => {
      if (e.repeat && toValue2(dedupe2))
        return;
      if (predicate(e))
        handler(e);
    };
    return useEventListener(target, eventName, listener, passive);
  }
  function cloneFnJSON(source) {
    return JSON.parse(JSON.stringify(source));
  }
  function useVModel(props, key, emit2, options = {}) {
    var _a, _b, _c;
    const {
      clone: clone4 = false,
      passive = false,
      eventName,
      deep = false,
      defaultValue,
      shouldEmit
    } = options;
    const vm = getCurrentInstance();
    const _emit = emit2 || (vm == null ? void 0 : vm.emit) || ((_a = vm == null ? void 0 : vm.$emit) == null ? void 0 : _a.bind(vm)) || ((_c = (_b = vm == null ? void 0 : vm.proxy) == null ? void 0 : _b.$emit) == null ? void 0 : _c.bind(vm == null ? void 0 : vm.proxy));
    let event = eventName;
    if (!key) {
      {
        key = "modelValue";
      }
    }
    event = event || `update:${key.toString()}`;
    const cloneFn = (val) => !clone4 ? val : typeof clone4 === "function" ? clone4(val) : cloneFnJSON(val);
    const getValue = () => isDef$1(props[key]) ? cloneFn(props[key]) : defaultValue;
    const triggerEmit = (value) => {
      if (shouldEmit) {
        if (shouldEmit(value))
          _emit(event, value);
      } else {
        _emit(event, value);
      }
    };
    if (passive) {
      const initialValue = getValue();
      const proxy = ref(initialValue);
      let isUpdating = false;
      watch2(
        () => props[key],
        (v) => {
          if (!isUpdating) {
            isUpdating = true;
            proxy.value = cloneFn(v);
            nextTick(() => isUpdating = false);
          }
        }
      );
      watch2(
        proxy,
        (v) => {
          if (!isUpdating && (v !== props[key] || deep))
            triggerEmit(v);
        },
        { deep }
      );
      return proxy;
    } else {
      return computed2({
        get() {
          return getValue();
        },
        set(value) {
          triggerEmit(value);
        }
      });
    }
  }
  var noop$2 = { value: () => {
  } };
  function dispatch() {
    for (var i = 0, n = arguments.length, _ = {}, t; i < n; ++i) {
      if (!(t = arguments[i] + "") || t in _ || /[\s.]/.test(t))
        throw new Error("illegal type: " + t);
      _[t] = [];
    }
    return new Dispatch(_);
  }
  function Dispatch(_) {
    this._ = _;
  }
  function parseTypenames$1(typenames, types) {
    return typenames.trim().split(/^|\s+/).map(function(t) {
      var name = "", i = t.indexOf(".");
      if (i >= 0)
        name = t.slice(i + 1), t = t.slice(0, i);
      if (t && !types.hasOwnProperty(t))
        throw new Error("unknown type: " + t);
      return { type: t, name };
    });
  }
  Dispatch.prototype = dispatch.prototype = {
    constructor: Dispatch,
    on: function(typename, callback) {
      var _ = this._, T = parseTypenames$1(typename + "", _), t, i = -1, n = T.length;
      if (arguments.length < 2) {
        while (++i < n)
          if ((t = (typename = T[i]).type) && (t = get$1(_[t], typename.name)))
            return t;
        return;
      }
      if (callback != null && typeof callback !== "function")
        throw new Error("invalid callback: " + callback);
      while (++i < n) {
        if (t = (typename = T[i]).type)
          _[t] = set$1(_[t], typename.name, callback);
        else if (callback == null)
          for (t in _)
            _[t] = set$1(_[t], typename.name, null);
      }
      return this;
    },
    copy: function() {
      var copy = {}, _ = this._;
      for (var t in _)
        copy[t] = _[t].slice();
      return new Dispatch(copy);
    },
    call: function(type, that) {
      if ((n = arguments.length - 2) > 0)
        for (var args = new Array(n), i = 0, n, t; i < n; ++i)
          args[i] = arguments[i + 2];
      if (!this._.hasOwnProperty(type))
        throw new Error("unknown type: " + type);
      for (t = this._[type], i = 0, n = t.length; i < n; ++i)
        t[i].value.apply(that, args);
    },
    apply: function(type, that, args) {
      if (!this._.hasOwnProperty(type))
        throw new Error("unknown type: " + type);
      for (var t = this._[type], i = 0, n = t.length; i < n; ++i)
        t[i].value.apply(that, args);
    }
  };
  function get$1(type, name) {
    for (var i = 0, n = type.length, c; i < n; ++i) {
      if ((c = type[i]).name === name) {
        return c.value;
      }
    }
  }
  function set$1(type, name, callback) {
    for (var i = 0, n = type.length; i < n; ++i) {
      if (type[i].name === name) {
        type[i] = noop$2, type = type.slice(0, i).concat(type.slice(i + 1));
        break;
      }
    }
    if (callback != null)
      type.push({ name, value: callback });
    return type;
  }
  var xhtml = "http://www.w3.org/1999/xhtml";
  var namespaces = {
    svg: "http://www.w3.org/2000/svg",
    xhtml,
    xlink: "http://www.w3.org/1999/xlink",
    xml: "http://www.w3.org/XML/1998/namespace",
    xmlns: "http://www.w3.org/2000/xmlns/"
  };
  function namespace(name) {
    var prefix = name += "", i = prefix.indexOf(":");
    if (i >= 0 && (prefix = name.slice(0, i)) !== "xmlns")
      name = name.slice(i + 1);
    return namespaces.hasOwnProperty(prefix) ? { space: namespaces[prefix], local: name } : name;
  }
  function creatorInherit(name) {
    return function() {
      var document2 = this.ownerDocument, uri = this.namespaceURI;
      return uri === xhtml && document2.documentElement.namespaceURI === xhtml ? document2.createElement(name) : document2.createElementNS(uri, name);
    };
  }
  function creatorFixed(fullname) {
    return function() {
      return this.ownerDocument.createElementNS(fullname.space, fullname.local);
    };
  }
  function creator(name) {
    var fullname = namespace(name);
    return (fullname.local ? creatorFixed : creatorInherit)(fullname);
  }
  function none() {
  }
  function selector(selector2) {
    return selector2 == null ? none : function() {
      return this.querySelector(selector2);
    };
  }
  function selection_select(select2) {
    if (typeof select2 !== "function")
      select2 = selector(select2);
    for (var groups = this._groups, m = groups.length, subgroups = new Array(m), j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, subgroup = subgroups[j] = new Array(n), node, subnode, i = 0; i < n; ++i) {
        if ((node = group[i]) && (subnode = select2.call(node, node.__data__, i, group))) {
          if ("__data__" in node)
            subnode.__data__ = node.__data__;
          subgroup[i] = subnode;
        }
      }
    }
    return new Selection$1(subgroups, this._parents);
  }
  function array(x) {
    return x == null ? [] : Array.isArray(x) ? x : Array.from(x);
  }
  function empty() {
    return [];
  }
  function selectorAll(selector2) {
    return selector2 == null ? empty : function() {
      return this.querySelectorAll(selector2);
    };
  }
  function arrayAll(select2) {
    return function() {
      return array(select2.apply(this, arguments));
    };
  }
  function selection_selectAll(select2) {
    if (typeof select2 === "function")
      select2 = arrayAll(select2);
    else
      select2 = selectorAll(select2);
    for (var groups = this._groups, m = groups.length, subgroups = [], parents = [], j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, node, i = 0; i < n; ++i) {
        if (node = group[i]) {
          subgroups.push(select2.call(node, node.__data__, i, group));
          parents.push(node);
        }
      }
    }
    return new Selection$1(subgroups, parents);
  }
  function matcher(selector2) {
    return function() {
      return this.matches(selector2);
    };
  }
  function childMatcher(selector2) {
    return function(node) {
      return node.matches(selector2);
    };
  }
  var find = Array.prototype.find;
  function childFind(match) {
    return function() {
      return find.call(this.children, match);
    };
  }
  function childFirst() {
    return this.firstElementChild;
  }
  function selection_selectChild(match) {
    return this.select(match == null ? childFirst : childFind(typeof match === "function" ? match : childMatcher(match)));
  }
  var filter = Array.prototype.filter;
  function children() {
    return Array.from(this.children);
  }
  function childrenFilter(match) {
    return function() {
      return filter.call(this.children, match);
    };
  }
  function selection_selectChildren(match) {
    return this.selectAll(match == null ? children : childrenFilter(typeof match === "function" ? match : childMatcher(match)));
  }
  function selection_filter(match) {
    if (typeof match !== "function")
      match = matcher(match);
    for (var groups = this._groups, m = groups.length, subgroups = new Array(m), j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, subgroup = subgroups[j] = [], node, i = 0; i < n; ++i) {
        if ((node = group[i]) && match.call(node, node.__data__, i, group)) {
          subgroup.push(node);
        }
      }
    }
    return new Selection$1(subgroups, this._parents);
  }
  function sparse(update) {
    return new Array(update.length);
  }
  function selection_enter() {
    return new Selection$1(this._enter || this._groups.map(sparse), this._parents);
  }
  function EnterNode(parent, datum2) {
    this.ownerDocument = parent.ownerDocument;
    this.namespaceURI = parent.namespaceURI;
    this._next = null;
    this._parent = parent;
    this.__data__ = datum2;
  }
  EnterNode.prototype = {
    constructor: EnterNode,
    appendChild: function(child) {
      return this._parent.insertBefore(child, this._next);
    },
    insertBefore: function(child, next) {
      return this._parent.insertBefore(child, next);
    },
    querySelector: function(selector2) {
      return this._parent.querySelector(selector2);
    },
    querySelectorAll: function(selector2) {
      return this._parent.querySelectorAll(selector2);
    }
  };
  function constant$3(x) {
    return function() {
      return x;
    };
  }
  function bindIndex(parent, group, enter, update, exit, data) {
    var i = 0, node, groupLength = group.length, dataLength = data.length;
    for (; i < dataLength; ++i) {
      if (node = group[i]) {
        node.__data__ = data[i];
        update[i] = node;
      } else {
        enter[i] = new EnterNode(parent, data[i]);
      }
    }
    for (; i < groupLength; ++i) {
      if (node = group[i]) {
        exit[i] = node;
      }
    }
  }
  function bindKey(parent, group, enter, update, exit, data, key) {
    var i, node, nodeByKeyValue = /* @__PURE__ */ new Map(), groupLength = group.length, dataLength = data.length, keyValues = new Array(groupLength), keyValue;
    for (i = 0; i < groupLength; ++i) {
      if (node = group[i]) {
        keyValues[i] = keyValue = key.call(node, node.__data__, i, group) + "";
        if (nodeByKeyValue.has(keyValue)) {
          exit[i] = node;
        } else {
          nodeByKeyValue.set(keyValue, node);
        }
      }
    }
    for (i = 0; i < dataLength; ++i) {
      keyValue = key.call(parent, data[i], i, data) + "";
      if (node = nodeByKeyValue.get(keyValue)) {
        update[i] = node;
        node.__data__ = data[i];
        nodeByKeyValue.delete(keyValue);
      } else {
        enter[i] = new EnterNode(parent, data[i]);
      }
    }
    for (i = 0; i < groupLength; ++i) {
      if ((node = group[i]) && nodeByKeyValue.get(keyValues[i]) === node) {
        exit[i] = node;
      }
    }
  }
  function datum(node) {
    return node.__data__;
  }
  function selection_data(value, key) {
    if (!arguments.length)
      return Array.from(this, datum);
    var bind = key ? bindKey : bindIndex, parents = this._parents, groups = this._groups;
    if (typeof value !== "function")
      value = constant$3(value);
    for (var m = groups.length, update = new Array(m), enter = new Array(m), exit = new Array(m), j = 0; j < m; ++j) {
      var parent = parents[j], group = groups[j], groupLength = group.length, data = arraylike(value.call(parent, parent && parent.__data__, j, parents)), dataLength = data.length, enterGroup = enter[j] = new Array(dataLength), updateGroup = update[j] = new Array(dataLength), exitGroup = exit[j] = new Array(groupLength);
      bind(parent, group, enterGroup, updateGroup, exitGroup, data, key);
      for (var i0 = 0, i1 = 0, previous, next; i0 < dataLength; ++i0) {
        if (previous = enterGroup[i0]) {
          if (i0 >= i1)
            i1 = i0 + 1;
          while (!(next = updateGroup[i1]) && ++i1 < dataLength)
            ;
          previous._next = next || null;
        }
      }
    }
    update = new Selection$1(update, parents);
    update._enter = enter;
    update._exit = exit;
    return update;
  }
  function arraylike(data) {
    return typeof data === "object" && "length" in data ? data : Array.from(data);
  }
  function selection_exit() {
    return new Selection$1(this._exit || this._groups.map(sparse), this._parents);
  }
  function selection_join(onenter, onupdate, onexit) {
    var enter = this.enter(), update = this, exit = this.exit();
    if (typeof onenter === "function") {
      enter = onenter(enter);
      if (enter)
        enter = enter.selection();
    } else {
      enter = enter.append(onenter + "");
    }
    if (onupdate != null) {
      update = onupdate(update);
      if (update)
        update = update.selection();
    }
    if (onexit == null)
      exit.remove();
    else
      onexit(exit);
    return enter && update ? enter.merge(update).order() : update;
  }
  function selection_merge(context) {
    var selection2 = context.selection ? context.selection() : context;
    for (var groups0 = this._groups, groups1 = selection2._groups, m0 = groups0.length, m1 = groups1.length, m = Math.min(m0, m1), merges = new Array(m0), j = 0; j < m; ++j) {
      for (var group0 = groups0[j], group1 = groups1[j], n = group0.length, merge = merges[j] = new Array(n), node, i = 0; i < n; ++i) {
        if (node = group0[i] || group1[i]) {
          merge[i] = node;
        }
      }
    }
    for (; j < m0; ++j) {
      merges[j] = groups0[j];
    }
    return new Selection$1(merges, this._parents);
  }
  function selection_order() {
    for (var groups = this._groups, j = -1, m = groups.length; ++j < m; ) {
      for (var group = groups[j], i = group.length - 1, next = group[i], node; --i >= 0; ) {
        if (node = group[i]) {
          if (next && node.compareDocumentPosition(next) ^ 4)
            next.parentNode.insertBefore(node, next);
          next = node;
        }
      }
    }
    return this;
  }
  function selection_sort(compare) {
    if (!compare)
      compare = ascending;
    function compareNode(a, b) {
      return a && b ? compare(a.__data__, b.__data__) : !a - !b;
    }
    for (var groups = this._groups, m = groups.length, sortgroups = new Array(m), j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, sortgroup = sortgroups[j] = new Array(n), node, i = 0; i < n; ++i) {
        if (node = group[i]) {
          sortgroup[i] = node;
        }
      }
      sortgroup.sort(compareNode);
    }
    return new Selection$1(sortgroups, this._parents).order();
  }
  function ascending(a, b) {
    return a < b ? -1 : a > b ? 1 : a >= b ? 0 : NaN;
  }
  function selection_call() {
    var callback = arguments[0];
    arguments[0] = this;
    callback.apply(null, arguments);
    return this;
  }
  function selection_nodes() {
    return Array.from(this);
  }
  function selection_node() {
    for (var groups = this._groups, j = 0, m = groups.length; j < m; ++j) {
      for (var group = groups[j], i = 0, n = group.length; i < n; ++i) {
        var node = group[i];
        if (node)
          return node;
      }
    }
    return null;
  }
  function selection_size() {
    let size = 0;
    for (const node of this)
      ++size;
    return size;
  }
  function selection_empty() {
    return !this.node();
  }
  function selection_each(callback) {
    for (var groups = this._groups, j = 0, m = groups.length; j < m; ++j) {
      for (var group = groups[j], i = 0, n = group.length, node; i < n; ++i) {
        if (node = group[i])
          callback.call(node, node.__data__, i, group);
      }
    }
    return this;
  }
  function attrRemove$1(name) {
    return function() {
      this.removeAttribute(name);
    };
  }
  function attrRemoveNS$1(fullname) {
    return function() {
      this.removeAttributeNS(fullname.space, fullname.local);
    };
  }
  function attrConstant$1(name, value) {
    return function() {
      this.setAttribute(name, value);
    };
  }
  function attrConstantNS$1(fullname, value) {
    return function() {
      this.setAttributeNS(fullname.space, fullname.local, value);
    };
  }
  function attrFunction$1(name, value) {
    return function() {
      var v = value.apply(this, arguments);
      if (v == null)
        this.removeAttribute(name);
      else
        this.setAttribute(name, v);
    };
  }
  function attrFunctionNS$1(fullname, value) {
    return function() {
      var v = value.apply(this, arguments);
      if (v == null)
        this.removeAttributeNS(fullname.space, fullname.local);
      else
        this.setAttributeNS(fullname.space, fullname.local, v);
    };
  }
  function selection_attr(name, value) {
    var fullname = namespace(name);
    if (arguments.length < 2) {
      var node = this.node();
      return fullname.local ? node.getAttributeNS(fullname.space, fullname.local) : node.getAttribute(fullname);
    }
    return this.each((value == null ? fullname.local ? attrRemoveNS$1 : attrRemove$1 : typeof value === "function" ? fullname.local ? attrFunctionNS$1 : attrFunction$1 : fullname.local ? attrConstantNS$1 : attrConstant$1)(fullname, value));
  }
  function defaultView(node) {
    return node.ownerDocument && node.ownerDocument.defaultView || node.document && node || node.defaultView;
  }
  function styleRemove$1(name) {
    return function() {
      this.style.removeProperty(name);
    };
  }
  function styleConstant$1(name, value, priority) {
    return function() {
      this.style.setProperty(name, value, priority);
    };
  }
  function styleFunction$1(name, value, priority) {
    return function() {
      var v = value.apply(this, arguments);
      if (v == null)
        this.style.removeProperty(name);
      else
        this.style.setProperty(name, v, priority);
    };
  }
  function selection_style(name, value, priority) {
    return arguments.length > 1 ? this.each((value == null ? styleRemove$1 : typeof value === "function" ? styleFunction$1 : styleConstant$1)(name, value, priority == null ? "" : priority)) : styleValue2(this.node(), name);
  }
  function styleValue2(node, name) {
    return node.style.getPropertyValue(name) || defaultView(node).getComputedStyle(node, null).getPropertyValue(name);
  }
  function propertyRemove(name) {
    return function() {
      delete this[name];
    };
  }
  function propertyConstant(name, value) {
    return function() {
      this[name] = value;
    };
  }
  function propertyFunction(name, value) {
    return function() {
      var v = value.apply(this, arguments);
      if (v == null)
        delete this[name];
      else
        this[name] = v;
    };
  }
  function selection_property(name, value) {
    return arguments.length > 1 ? this.each((value == null ? propertyRemove : typeof value === "function" ? propertyFunction : propertyConstant)(name, value)) : this.node()[name];
  }
  function classArray(string) {
    return string.trim().split(/^|\s+/);
  }
  function classList(node) {
    return node.classList || new ClassList(node);
  }
  function ClassList(node) {
    this._node = node;
    this._names = classArray(node.getAttribute("class") || "");
  }
  ClassList.prototype = {
    add: function(name) {
      var i = this._names.indexOf(name);
      if (i < 0) {
        this._names.push(name);
        this._node.setAttribute("class", this._names.join(" "));
      }
    },
    remove: function(name) {
      var i = this._names.indexOf(name);
      if (i >= 0) {
        this._names.splice(i, 1);
        this._node.setAttribute("class", this._names.join(" "));
      }
    },
    contains: function(name) {
      return this._names.indexOf(name) >= 0;
    }
  };
  function classedAdd(node, names) {
    var list = classList(node), i = -1, n = names.length;
    while (++i < n)
      list.add(names[i]);
  }
  function classedRemove(node, names) {
    var list = classList(node), i = -1, n = names.length;
    while (++i < n)
      list.remove(names[i]);
  }
  function classedTrue(names) {
    return function() {
      classedAdd(this, names);
    };
  }
  function classedFalse(names) {
    return function() {
      classedRemove(this, names);
    };
  }
  function classedFunction(names, value) {
    return function() {
      (value.apply(this, arguments) ? classedAdd : classedRemove)(this, names);
    };
  }
  function selection_classed(name, value) {
    var names = classArray(name + "");
    if (arguments.length < 2) {
      var list = classList(this.node()), i = -1, n = names.length;
      while (++i < n)
        if (!list.contains(names[i]))
          return false;
      return true;
    }
    return this.each((typeof value === "function" ? classedFunction : value ? classedTrue : classedFalse)(names, value));
  }
  function textRemove() {
    this.textContent = "";
  }
  function textConstant$1(value) {
    return function() {
      this.textContent = value;
    };
  }
  function textFunction$1(value) {
    return function() {
      var v = value.apply(this, arguments);
      this.textContent = v == null ? "" : v;
    };
  }
  function selection_text(value) {
    return arguments.length ? this.each(value == null ? textRemove : (typeof value === "function" ? textFunction$1 : textConstant$1)(value)) : this.node().textContent;
  }
  function htmlRemove() {
    this.innerHTML = "";
  }
  function htmlConstant(value) {
    return function() {
      this.innerHTML = value;
    };
  }
  function htmlFunction(value) {
    return function() {
      var v = value.apply(this, arguments);
      this.innerHTML = v == null ? "" : v;
    };
  }
  function selection_html(value) {
    return arguments.length ? this.each(value == null ? htmlRemove : (typeof value === "function" ? htmlFunction : htmlConstant)(value)) : this.node().innerHTML;
  }
  function raise() {
    if (this.nextSibling)
      this.parentNode.appendChild(this);
  }
  function selection_raise() {
    return this.each(raise);
  }
  function lower() {
    if (this.previousSibling)
      this.parentNode.insertBefore(this, this.parentNode.firstChild);
  }
  function selection_lower() {
    return this.each(lower);
  }
  function selection_append(name) {
    var create2 = typeof name === "function" ? name : creator(name);
    return this.select(function() {
      return this.appendChild(create2.apply(this, arguments));
    });
  }
  function constantNull() {
    return null;
  }
  function selection_insert(name, before) {
    var create2 = typeof name === "function" ? name : creator(name), select2 = before == null ? constantNull : typeof before === "function" ? before : selector(before);
    return this.select(function() {
      return this.insertBefore(create2.apply(this, arguments), select2.apply(this, arguments) || null);
    });
  }
  function remove2() {
    var parent = this.parentNode;
    if (parent)
      parent.removeChild(this);
  }
  function selection_remove() {
    return this.each(remove2);
  }
  function selection_cloneShallow() {
    var clone4 = this.cloneNode(false), parent = this.parentNode;
    return parent ? parent.insertBefore(clone4, this.nextSibling) : clone4;
  }
  function selection_cloneDeep() {
    var clone4 = this.cloneNode(true), parent = this.parentNode;
    return parent ? parent.insertBefore(clone4, this.nextSibling) : clone4;
  }
  function selection_clone(deep) {
    return this.select(deep ? selection_cloneDeep : selection_cloneShallow);
  }
  function selection_datum(value) {
    return arguments.length ? this.property("__data__", value) : this.node().__data__;
  }
  function contextListener(listener) {
    return function(event) {
      listener.call(this, event, this.__data__);
    };
  }
  function parseTypenames(typenames) {
    return typenames.trim().split(/^|\s+/).map(function(t) {
      var name = "", i = t.indexOf(".");
      if (i >= 0)
        name = t.slice(i + 1), t = t.slice(0, i);
      return { type: t, name };
    });
  }
  function onRemove(typename) {
    return function() {
      var on = this.__on;
      if (!on)
        return;
      for (var j = 0, i = -1, m = on.length, o; j < m; ++j) {
        if (o = on[j], (!typename.type || o.type === typename.type) && o.name === typename.name) {
          this.removeEventListener(o.type, o.listener, o.options);
        } else {
          on[++i] = o;
        }
      }
      if (++i)
        on.length = i;
      else
        delete this.__on;
    };
  }
  function onAdd(typename, value, options) {
    return function() {
      var on = this.__on, o, listener = contextListener(value);
      if (on)
        for (var j = 0, m = on.length; j < m; ++j) {
          if ((o = on[j]).type === typename.type && o.name === typename.name) {
            this.removeEventListener(o.type, o.listener, o.options);
            this.addEventListener(o.type, o.listener = listener, o.options = options);
            o.value = value;
            return;
          }
        }
      this.addEventListener(typename.type, listener, options);
      o = { type: typename.type, name: typename.name, value, listener, options };
      if (!on)
        this.__on = [o];
      else
        on.push(o);
    };
  }
  function selection_on(typename, value, options) {
    var typenames = parseTypenames(typename + ""), i, n = typenames.length, t;
    if (arguments.length < 2) {
      var on = this.node().__on;
      if (on)
        for (var j = 0, m = on.length, o; j < m; ++j) {
          for (i = 0, o = on[j]; i < n; ++i) {
            if ((t = typenames[i]).type === o.type && t.name === o.name) {
              return o.value;
            }
          }
        }
      return;
    }
    on = value ? onAdd : onRemove;
    for (i = 0; i < n; ++i)
      this.each(on(typenames[i], value, options));
    return this;
  }
  function dispatchEvent(node, type, params) {
    var window2 = defaultView(node), event = window2.CustomEvent;
    if (typeof event === "function") {
      event = new event(type, params);
    } else {
      event = window2.document.createEvent("Event");
      if (params)
        event.initEvent(type, params.bubbles, params.cancelable), event.detail = params.detail;
      else
        event.initEvent(type, false, false);
    }
    node.dispatchEvent(event);
  }
  function dispatchConstant(type, params) {
    return function() {
      return dispatchEvent(this, type, params);
    };
  }
  function dispatchFunction(type, params) {
    return function() {
      return dispatchEvent(this, type, params.apply(this, arguments));
    };
  }
  function selection_dispatch(type, params) {
    return this.each((typeof params === "function" ? dispatchFunction : dispatchConstant)(type, params));
  }
  function* selection_iterator() {
    for (var groups = this._groups, j = 0, m = groups.length; j < m; ++j) {
      for (var group = groups[j], i = 0, n = group.length, node; i < n; ++i) {
        if (node = group[i])
          yield node;
      }
    }
  }
  var root = [null];
  function Selection$1(groups, parents) {
    this._groups = groups;
    this._parents = parents;
  }
  function selection() {
    return new Selection$1([[document.documentElement]], root);
  }
  function selection_selection() {
    return this;
  }
  Selection$1.prototype = selection.prototype = {
    constructor: Selection$1,
    select: selection_select,
    selectAll: selection_selectAll,
    selectChild: selection_selectChild,
    selectChildren: selection_selectChildren,
    filter: selection_filter,
    data: selection_data,
    enter: selection_enter,
    exit: selection_exit,
    join: selection_join,
    merge: selection_merge,
    selection: selection_selection,
    order: selection_order,
    sort: selection_sort,
    call: selection_call,
    nodes: selection_nodes,
    node: selection_node,
    size: selection_size,
    empty: selection_empty,
    each: selection_each,
    attr: selection_attr,
    style: selection_style,
    property: selection_property,
    classed: selection_classed,
    text: selection_text,
    html: selection_html,
    raise: selection_raise,
    lower: selection_lower,
    append: selection_append,
    insert: selection_insert,
    remove: selection_remove,
    clone: selection_clone,
    datum: selection_datum,
    on: selection_on,
    dispatch: selection_dispatch,
    [Symbol.iterator]: selection_iterator
  };
  function select(selector2) {
    return typeof selector2 === "string" ? new Selection$1([[document.querySelector(selector2)]], [document.documentElement]) : new Selection$1([[selector2]], root);
  }
  function sourceEvent(event) {
    let sourceEvent2;
    while (sourceEvent2 = event.sourceEvent)
      event = sourceEvent2;
    return event;
  }
  function pointer(event, node) {
    event = sourceEvent(event);
    if (node === void 0)
      node = event.currentTarget;
    if (node) {
      var svg = node.ownerSVGElement || node;
      if (svg.createSVGPoint) {
        var point = svg.createSVGPoint();
        point.x = event.clientX, point.y = event.clientY;
        point = point.matrixTransform(node.getScreenCTM().inverse());
        return [point.x, point.y];
      }
      if (node.getBoundingClientRect) {
        var rect = node.getBoundingClientRect();
        return [event.clientX - rect.left - node.clientLeft, event.clientY - rect.top - node.clientTop];
      }
    }
    return [event.pageX, event.pageY];
  }
  var nonpassive = { passive: false };
  var nonpassivecapture = { capture: true, passive: false };
  function nopropagation$1(event) {
    event.stopImmediatePropagation();
  }
  function noevent$1(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  function dragDisable(view) {
    var root2 = view.document.documentElement, selection2 = select(view).on("dragstart.drag", noevent$1, nonpassivecapture);
    if ("onselectstart" in root2) {
      selection2.on("selectstart.drag", noevent$1, nonpassivecapture);
    } else {
      root2.__noselect = root2.style.MozUserSelect;
      root2.style.MozUserSelect = "none";
    }
  }
  function yesdrag(view, noclick) {
    var root2 = view.document.documentElement, selection2 = select(view).on("dragstart.drag", null);
    if (noclick) {
      selection2.on("click.drag", noevent$1, nonpassivecapture);
      setTimeout(function() {
        selection2.on("click.drag", null);
      }, 0);
    }
    if ("onselectstart" in root2) {
      selection2.on("selectstart.drag", null);
    } else {
      root2.style.MozUserSelect = root2.__noselect;
      delete root2.__noselect;
    }
  }
  var constant$2 = (x) => () => x;
  function DragEvent(type, {
    sourceEvent: sourceEvent2,
    subject,
    target,
    identifier,
    active,
    x,
    y,
    dx,
    dy,
    dispatch: dispatch2
  }) {
    Object.defineProperties(this, {
      type: { value: type, enumerable: true, configurable: true },
      sourceEvent: { value: sourceEvent2, enumerable: true, configurable: true },
      subject: { value: subject, enumerable: true, configurable: true },
      target: { value: target, enumerable: true, configurable: true },
      identifier: { value: identifier, enumerable: true, configurable: true },
      active: { value: active, enumerable: true, configurable: true },
      x: { value: x, enumerable: true, configurable: true },
      y: { value: y, enumerable: true, configurable: true },
      dx: { value: dx, enumerable: true, configurable: true },
      dy: { value: dy, enumerable: true, configurable: true },
      _: { value: dispatch2 }
    });
  }
  DragEvent.prototype.on = function() {
    var value = this._.on.apply(this._, arguments);
    return value === this._ ? this : value;
  };
  function defaultFilter$1(event) {
    return !event.ctrlKey && !event.button;
  }
  function defaultContainer() {
    return this.parentNode;
  }
  function defaultSubject(event, d) {
    return d == null ? { x: event.x, y: event.y } : d;
  }
  function defaultTouchable$1() {
    return navigator.maxTouchPoints || "ontouchstart" in this;
  }
  function drag() {
    var filter2 = defaultFilter$1, container = defaultContainer, subject = defaultSubject, touchable = defaultTouchable$1, gestures = {}, listeners = dispatch("start", "drag", "end"), active = 0, mousedownx, mousedowny, mousemoving, touchending, clickDistance2 = 0;
    function drag2(selection2) {
      selection2.on("mousedown.drag", mousedowned).filter(touchable).on("touchstart.drag", touchstarted).on("touchmove.drag", touchmoved, nonpassive).on("touchend.drag touchcancel.drag", touchended).style("touch-action", "none").style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
    }
    function mousedowned(event, d) {
      if (touchending || !filter2.call(this, event, d))
        return;
      var gesture = beforestart(this, container.call(this, event, d), event, d, "mouse");
      if (!gesture)
        return;
      select(event.view).on("mousemove.drag", mousemoved, nonpassivecapture).on("mouseup.drag", mouseupped, nonpassivecapture);
      dragDisable(event.view);
      nopropagation$1(event);
      mousemoving = false;
      mousedownx = event.clientX;
      mousedowny = event.clientY;
      gesture("start", event);
    }
    function mousemoved(event) {
      noevent$1(event);
      if (!mousemoving) {
        var dx = event.clientX - mousedownx, dy = event.clientY - mousedowny;
        mousemoving = dx * dx + dy * dy > clickDistance2;
      }
      gestures.mouse("drag", event);
    }
    function mouseupped(event) {
      select(event.view).on("mousemove.drag mouseup.drag", null);
      yesdrag(event.view, mousemoving);
      noevent$1(event);
      gestures.mouse("end", event);
    }
    function touchstarted(event, d) {
      if (!filter2.call(this, event, d))
        return;
      var touches = event.changedTouches, c = container.call(this, event, d), n = touches.length, i, gesture;
      for (i = 0; i < n; ++i) {
        if (gesture = beforestart(this, c, event, d, touches[i].identifier, touches[i])) {
          nopropagation$1(event);
          gesture("start", event, touches[i]);
        }
      }
    }
    function touchmoved(event) {
      var touches = event.changedTouches, n = touches.length, i, gesture;
      for (i = 0; i < n; ++i) {
        if (gesture = gestures[touches[i].identifier]) {
          noevent$1(event);
          gesture("drag", event, touches[i]);
        }
      }
    }
    function touchended(event) {
      var touches = event.changedTouches, n = touches.length, i, gesture;
      if (touchending)
        clearTimeout(touchending);
      touchending = setTimeout(function() {
        touchending = null;
      }, 500);
      for (i = 0; i < n; ++i) {
        if (gesture = gestures[touches[i].identifier]) {
          nopropagation$1(event);
          gesture("end", event, touches[i]);
        }
      }
    }
    function beforestart(that, container2, event, d, identifier, touch) {
      var dispatch2 = listeners.copy(), p2 = pointer(touch || event, container2), dx, dy, s;
      if ((s = subject.call(that, new DragEvent("beforestart", {
        sourceEvent: event,
        target: drag2,
        identifier,
        active,
        x: p2[0],
        y: p2[1],
        dx: 0,
        dy: 0,
        dispatch: dispatch2
      }), d)) == null)
        return;
      dx = s.x - p2[0] || 0;
      dy = s.y - p2[1] || 0;
      return function gesture(type, event2, touch2) {
        var p0 = p2, n;
        switch (type) {
          case "start":
            gestures[identifier] = gesture, n = active++;
            break;
          case "end":
            delete gestures[identifier], --active;
          case "drag":
            p2 = pointer(touch2 || event2, container2), n = active;
            break;
        }
        dispatch2.call(
          type,
          that,
          new DragEvent(type, {
            sourceEvent: event2,
            subject: s,
            target: drag2,
            identifier,
            active: n,
            x: p2[0] + dx,
            y: p2[1] + dy,
            dx: p2[0] - p0[0],
            dy: p2[1] - p0[1],
            dispatch: dispatch2
          }),
          d
        );
      };
    }
    drag2.filter = function(_) {
      return arguments.length ? (filter2 = typeof _ === "function" ? _ : constant$2(!!_), drag2) : filter2;
    };
    drag2.container = function(_) {
      return arguments.length ? (container = typeof _ === "function" ? _ : constant$2(_), drag2) : container;
    };
    drag2.subject = function(_) {
      return arguments.length ? (subject = typeof _ === "function" ? _ : constant$2(_), drag2) : subject;
    };
    drag2.touchable = function(_) {
      return arguments.length ? (touchable = typeof _ === "function" ? _ : constant$2(!!_), drag2) : touchable;
    };
    drag2.on = function() {
      var value = listeners.on.apply(listeners, arguments);
      return value === listeners ? drag2 : value;
    };
    drag2.clickDistance = function(_) {
      return arguments.length ? (clickDistance2 = (_ = +_) * _, drag2) : Math.sqrt(clickDistance2);
    };
    return drag2;
  }
  function define(constructor, factory, prototype) {
    constructor.prototype = factory.prototype = prototype;
    prototype.constructor = constructor;
  }
  function extend2(parent, definition) {
    var prototype = Object.create(parent.prototype);
    for (var key in definition)
      prototype[key] = definition[key];
    return prototype;
  }
  function Color() {
  }
  var darker = 0.7;
  var brighter = 1 / darker;
  var reI = "\\s*([+-]?\\d+)\\s*";
  var reN = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*";
  var reP = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*";
  var reHex = /^#([0-9a-f]{3,8})$/;
  var reRgbInteger = new RegExp(`^rgb\\(${reI},${reI},${reI}\\)$`);
  var reRgbPercent = new RegExp(`^rgb\\(${reP},${reP},${reP}\\)$`);
  var reRgbaInteger = new RegExp(`^rgba\\(${reI},${reI},${reI},${reN}\\)$`);
  var reRgbaPercent = new RegExp(`^rgba\\(${reP},${reP},${reP},${reN}\\)$`);
  var reHslPercent = new RegExp(`^hsl\\(${reN},${reP},${reP}\\)$`);
  var reHslaPercent = new RegExp(`^hsla\\(${reN},${reP},${reP},${reN}\\)$`);
  var named = {
    aliceblue: 15792383,
    antiquewhite: 16444375,
    aqua: 65535,
    aquamarine: 8388564,
    azure: 15794175,
    beige: 16119260,
    bisque: 16770244,
    black: 0,
    blanchedalmond: 16772045,
    blue: 255,
    blueviolet: 9055202,
    brown: 10824234,
    burlywood: 14596231,
    cadetblue: 6266528,
    chartreuse: 8388352,
    chocolate: 13789470,
    coral: 16744272,
    cornflowerblue: 6591981,
    cornsilk: 16775388,
    crimson: 14423100,
    cyan: 65535,
    darkblue: 139,
    darkcyan: 35723,
    darkgoldenrod: 12092939,
    darkgray: 11119017,
    darkgreen: 25600,
    darkgrey: 11119017,
    darkkhaki: 12433259,
    darkmagenta: 9109643,
    darkolivegreen: 5597999,
    darkorange: 16747520,
    darkorchid: 10040012,
    darkred: 9109504,
    darksalmon: 15308410,
    darkseagreen: 9419919,
    darkslateblue: 4734347,
    darkslategray: 3100495,
    darkslategrey: 3100495,
    darkturquoise: 52945,
    darkviolet: 9699539,
    deeppink: 16716947,
    deepskyblue: 49151,
    dimgray: 6908265,
    dimgrey: 6908265,
    dodgerblue: 2003199,
    firebrick: 11674146,
    floralwhite: 16775920,
    forestgreen: 2263842,
    fuchsia: 16711935,
    gainsboro: 14474460,
    ghostwhite: 16316671,
    gold: 16766720,
    goldenrod: 14329120,
    gray: 8421504,
    green: 32768,
    greenyellow: 11403055,
    grey: 8421504,
    honeydew: 15794160,
    hotpink: 16738740,
    indianred: 13458524,
    indigo: 4915330,
    ivory: 16777200,
    khaki: 15787660,
    lavender: 15132410,
    lavenderblush: 16773365,
    lawngreen: 8190976,
    lemonchiffon: 16775885,
    lightblue: 11393254,
    lightcoral: 15761536,
    lightcyan: 14745599,
    lightgoldenrodyellow: 16448210,
    lightgray: 13882323,
    lightgreen: 9498256,
    lightgrey: 13882323,
    lightpink: 16758465,
    lightsalmon: 16752762,
    lightseagreen: 2142890,
    lightskyblue: 8900346,
    lightslategray: 7833753,
    lightslategrey: 7833753,
    lightsteelblue: 11584734,
    lightyellow: 16777184,
    lime: 65280,
    limegreen: 3329330,
    linen: 16445670,
    magenta: 16711935,
    maroon: 8388608,
    mediumaquamarine: 6737322,
    mediumblue: 205,
    mediumorchid: 12211667,
    mediumpurple: 9662683,
    mediumseagreen: 3978097,
    mediumslateblue: 8087790,
    mediumspringgreen: 64154,
    mediumturquoise: 4772300,
    mediumvioletred: 13047173,
    midnightblue: 1644912,
    mintcream: 16121850,
    mistyrose: 16770273,
    moccasin: 16770229,
    navajowhite: 16768685,
    navy: 128,
    oldlace: 16643558,
    olive: 8421376,
    olivedrab: 7048739,
    orange: 16753920,
    orangered: 16729344,
    orchid: 14315734,
    palegoldenrod: 15657130,
    palegreen: 10025880,
    paleturquoise: 11529966,
    palevioletred: 14381203,
    papayawhip: 16773077,
    peachpuff: 16767673,
    peru: 13468991,
    pink: 16761035,
    plum: 14524637,
    powderblue: 11591910,
    purple: 8388736,
    rebeccapurple: 6697881,
    red: 16711680,
    rosybrown: 12357519,
    royalblue: 4286945,
    saddlebrown: 9127187,
    salmon: 16416882,
    sandybrown: 16032864,
    seagreen: 3050327,
    seashell: 16774638,
    sienna: 10506797,
    silver: 12632256,
    skyblue: 8900331,
    slateblue: 6970061,
    slategray: 7372944,
    slategrey: 7372944,
    snow: 16775930,
    springgreen: 65407,
    steelblue: 4620980,
    tan: 13808780,
    teal: 32896,
    thistle: 14204888,
    tomato: 16737095,
    turquoise: 4251856,
    violet: 15631086,
    wheat: 16113331,
    white: 16777215,
    whitesmoke: 16119285,
    yellow: 16776960,
    yellowgreen: 10145074
  };
  define(Color, color, {
    copy(channels) {
      return Object.assign(new this.constructor(), this, channels);
    },
    displayable() {
      return this.rgb().displayable();
    },
    hex: color_formatHex,
    // Deprecated! Use color.formatHex.
    formatHex: color_formatHex,
    formatHex8: color_formatHex8,
    formatHsl: color_formatHsl,
    formatRgb: color_formatRgb,
    toString: color_formatRgb
  });
  function color_formatHex() {
    return this.rgb().formatHex();
  }
  function color_formatHex8() {
    return this.rgb().formatHex8();
  }
  function color_formatHsl() {
    return hslConvert(this).formatHsl();
  }
  function color_formatRgb() {
    return this.rgb().formatRgb();
  }
  function color(format) {
    var m, l;
    format = (format + "").trim().toLowerCase();
    return (m = reHex.exec(format)) ? (l = m[1].length, m = parseInt(m[1], 16), l === 6 ? rgbn(m) : l === 3 ? new Rgb(m >> 8 & 15 | m >> 4 & 240, m >> 4 & 15 | m & 240, (m & 15) << 4 | m & 15, 1) : l === 8 ? rgba(m >> 24 & 255, m >> 16 & 255, m >> 8 & 255, (m & 255) / 255) : l === 4 ? rgba(m >> 12 & 15 | m >> 8 & 240, m >> 8 & 15 | m >> 4 & 240, m >> 4 & 15 | m & 240, ((m & 15) << 4 | m & 15) / 255) : null) : (m = reRgbInteger.exec(format)) ? new Rgb(m[1], m[2], m[3], 1) : (m = reRgbPercent.exec(format)) ? new Rgb(m[1] * 255 / 100, m[2] * 255 / 100, m[3] * 255 / 100, 1) : (m = reRgbaInteger.exec(format)) ? rgba(m[1], m[2], m[3], m[4]) : (m = reRgbaPercent.exec(format)) ? rgba(m[1] * 255 / 100, m[2] * 255 / 100, m[3] * 255 / 100, m[4]) : (m = reHslPercent.exec(format)) ? hsla(m[1], m[2] / 100, m[3] / 100, 1) : (m = reHslaPercent.exec(format)) ? hsla(m[1], m[2] / 100, m[3] / 100, m[4]) : named.hasOwnProperty(format) ? rgbn(named[format]) : format === "transparent" ? new Rgb(NaN, NaN, NaN, 0) : null;
  }
  function rgbn(n) {
    return new Rgb(n >> 16 & 255, n >> 8 & 255, n & 255, 1);
  }
  function rgba(r, g, b, a) {
    if (a <= 0)
      r = g = b = NaN;
    return new Rgb(r, g, b, a);
  }
  function rgbConvert(o) {
    if (!(o instanceof Color))
      o = color(o);
    if (!o)
      return new Rgb();
    o = o.rgb();
    return new Rgb(o.r, o.g, o.b, o.opacity);
  }
  function rgb(r, g, b, opacity) {
    return arguments.length === 1 ? rgbConvert(r) : new Rgb(r, g, b, opacity == null ? 1 : opacity);
  }
  function Rgb(r, g, b, opacity) {
    this.r = +r;
    this.g = +g;
    this.b = +b;
    this.opacity = +opacity;
  }
  define(Rgb, rgb, extend2(Color, {
    brighter(k) {
      k = k == null ? brighter : Math.pow(brighter, k);
      return new Rgb(this.r * k, this.g * k, this.b * k, this.opacity);
    },
    darker(k) {
      k = k == null ? darker : Math.pow(darker, k);
      return new Rgb(this.r * k, this.g * k, this.b * k, this.opacity);
    },
    rgb() {
      return this;
    },
    clamp() {
      return new Rgb(clampi(this.r), clampi(this.g), clampi(this.b), clampa(this.opacity));
    },
    displayable() {
      return -0.5 <= this.r && this.r < 255.5 && (-0.5 <= this.g && this.g < 255.5) && (-0.5 <= this.b && this.b < 255.5) && (0 <= this.opacity && this.opacity <= 1);
    },
    hex: rgb_formatHex,
    // Deprecated! Use color.formatHex.
    formatHex: rgb_formatHex,
    formatHex8: rgb_formatHex8,
    formatRgb: rgb_formatRgb,
    toString: rgb_formatRgb
  }));
  function rgb_formatHex() {
    return `#${hex(this.r)}${hex(this.g)}${hex(this.b)}`;
  }
  function rgb_formatHex8() {
    return `#${hex(this.r)}${hex(this.g)}${hex(this.b)}${hex((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
  }
  function rgb_formatRgb() {
    const a = clampa(this.opacity);
    return `${a === 1 ? "rgb(" : "rgba("}${clampi(this.r)}, ${clampi(this.g)}, ${clampi(this.b)}${a === 1 ? ")" : `, ${a})`}`;
  }
  function clampa(opacity) {
    return isNaN(opacity) ? 1 : Math.max(0, Math.min(1, opacity));
  }
  function clampi(value) {
    return Math.max(0, Math.min(255, Math.round(value) || 0));
  }
  function hex(value) {
    value = clampi(value);
    return (value < 16 ? "0" : "") + value.toString(16);
  }
  function hsla(h2, s, l, a) {
    if (a <= 0)
      h2 = s = l = NaN;
    else if (l <= 0 || l >= 1)
      h2 = s = NaN;
    else if (s <= 0)
      h2 = NaN;
    return new Hsl(h2, s, l, a);
  }
  function hslConvert(o) {
    if (o instanceof Hsl)
      return new Hsl(o.h, o.s, o.l, o.opacity);
    if (!(o instanceof Color))
      o = color(o);
    if (!o)
      return new Hsl();
    if (o instanceof Hsl)
      return o;
    o = o.rgb();
    var r = o.r / 255, g = o.g / 255, b = o.b / 255, min = Math.min(r, g, b), max = Math.max(r, g, b), h2 = NaN, s = max - min, l = (max + min) / 2;
    if (s) {
      if (r === max)
        h2 = (g - b) / s + (g < b) * 6;
      else if (g === max)
        h2 = (b - r) / s + 2;
      else
        h2 = (r - g) / s + 4;
      s /= l < 0.5 ? max + min : 2 - max - min;
      h2 *= 60;
    } else {
      s = l > 0 && l < 1 ? 0 : h2;
    }
    return new Hsl(h2, s, l, o.opacity);
  }
  function hsl(h2, s, l, opacity) {
    return arguments.length === 1 ? hslConvert(h2) : new Hsl(h2, s, l, opacity == null ? 1 : opacity);
  }
  function Hsl(h2, s, l, opacity) {
    this.h = +h2;
    this.s = +s;
    this.l = +l;
    this.opacity = +opacity;
  }
  define(Hsl, hsl, extend2(Color, {
    brighter(k) {
      k = k == null ? brighter : Math.pow(brighter, k);
      return new Hsl(this.h, this.s, this.l * k, this.opacity);
    },
    darker(k) {
      k = k == null ? darker : Math.pow(darker, k);
      return new Hsl(this.h, this.s, this.l * k, this.opacity);
    },
    rgb() {
      var h2 = this.h % 360 + (this.h < 0) * 360, s = isNaN(h2) || isNaN(this.s) ? 0 : this.s, l = this.l, m2 = l + (l < 0.5 ? l : 1 - l) * s, m1 = 2 * l - m2;
      return new Rgb(
        hsl2rgb(h2 >= 240 ? h2 - 240 : h2 + 120, m1, m2),
        hsl2rgb(h2, m1, m2),
        hsl2rgb(h2 < 120 ? h2 + 240 : h2 - 120, m1, m2),
        this.opacity
      );
    },
    clamp() {
      return new Hsl(clamph(this.h), clampt(this.s), clampt(this.l), clampa(this.opacity));
    },
    displayable() {
      return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && (0 <= this.l && this.l <= 1) && (0 <= this.opacity && this.opacity <= 1);
    },
    formatHsl() {
      const a = clampa(this.opacity);
      return `${a === 1 ? "hsl(" : "hsla("}${clamph(this.h)}, ${clampt(this.s) * 100}%, ${clampt(this.l) * 100}%${a === 1 ? ")" : `, ${a})`}`;
    }
  }));
  function clamph(value) {
    value = (value || 0) % 360;
    return value < 0 ? value + 360 : value;
  }
  function clampt(value) {
    return Math.max(0, Math.min(1, value || 0));
  }
  function hsl2rgb(h2, m1, m2) {
    return (h2 < 60 ? m1 + (m2 - m1) * h2 / 60 : h2 < 180 ? m2 : h2 < 240 ? m1 + (m2 - m1) * (240 - h2) / 60 : m1) * 255;
  }
  var constant$1 = (x) => () => x;
  function linear(a, d) {
    return function(t) {
      return a + t * d;
    };
  }
  function exponential(a, b, y) {
    return a = Math.pow(a, y), b = Math.pow(b, y) - a, y = 1 / y, function(t) {
      return Math.pow(a + t * b, y);
    };
  }
  function gamma(y) {
    return (y = +y) === 1 ? nogamma : function(a, b) {
      return b - a ? exponential(a, b, y) : constant$1(isNaN(a) ? b : a);
    };
  }
  function nogamma(a, b) {
    var d = b - a;
    return d ? linear(a, d) : constant$1(isNaN(a) ? b : a);
  }
  var interpolateRgb = (function rgbGamma(y) {
    var color2 = gamma(y);
    function rgb$1(start2, end) {
      var r = color2((start2 = rgb(start2)).r, (end = rgb(end)).r), g = color2(start2.g, end.g), b = color2(start2.b, end.b), opacity = nogamma(start2.opacity, end.opacity);
      return function(t) {
        start2.r = r(t);
        start2.g = g(t);
        start2.b = b(t);
        start2.opacity = opacity(t);
        return start2 + "";
      };
    }
    rgb$1.gamma = rgbGamma;
    return rgb$1;
  })(1);
  function numberArray(a, b) {
    if (!b)
      b = [];
    var n = a ? Math.min(b.length, a.length) : 0, c = b.slice(), i;
    return function(t) {
      for (i = 0; i < n; ++i)
        c[i] = a[i] * (1 - t) + b[i] * t;
      return c;
    };
  }
  function isNumberArray(x) {
    return ArrayBuffer.isView(x) && !(x instanceof DataView);
  }
  function genericArray(a, b) {
    var nb = b ? b.length : 0, na = a ? Math.min(nb, a.length) : 0, x = new Array(na), c = new Array(nb), i;
    for (i = 0; i < na; ++i)
      x[i] = interpolate$1(a[i], b[i]);
    for (; i < nb; ++i)
      c[i] = b[i];
    return function(t) {
      for (i = 0; i < na; ++i)
        c[i] = x[i](t);
      return c;
    };
  }
  function date(a, b) {
    var d = /* @__PURE__ */ new Date();
    return a = +a, b = +b, function(t) {
      return d.setTime(a * (1 - t) + b * t), d;
    };
  }
  function interpolateNumber(a, b) {
    return a = +a, b = +b, function(t) {
      return a * (1 - t) + b * t;
    };
  }
  function object(a, b) {
    var i = {}, c = {}, k;
    if (a === null || typeof a !== "object")
      a = {};
    if (b === null || typeof b !== "object")
      b = {};
    for (k in b) {
      if (k in a) {
        i[k] = interpolate$1(a[k], b[k]);
      } else {
        c[k] = b[k];
      }
    }
    return function(t) {
      for (k in i)
        c[k] = i[k](t);
      return c;
    };
  }
  var reA = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g;
  var reB = new RegExp(reA.source, "g");
  function zero(b) {
    return function() {
      return b;
    };
  }
  function one(b) {
    return function(t) {
      return b(t) + "";
    };
  }
  function interpolateString(a, b) {
    var bi = reA.lastIndex = reB.lastIndex = 0, am, bm, bs, i = -1, s = [], q = [];
    a = a + "", b = b + "";
    while ((am = reA.exec(a)) && (bm = reB.exec(b))) {
      if ((bs = bm.index) > bi) {
        bs = b.slice(bi, bs);
        if (s[i])
          s[i] += bs;
        else
          s[++i] = bs;
      }
      if ((am = am[0]) === (bm = bm[0])) {
        if (s[i])
          s[i] += bm;
        else
          s[++i] = bm;
      } else {
        s[++i] = null;
        q.push({ i, x: interpolateNumber(am, bm) });
      }
      bi = reB.lastIndex;
    }
    if (bi < b.length) {
      bs = b.slice(bi);
      if (s[i])
        s[i] += bs;
      else
        s[++i] = bs;
    }
    return s.length < 2 ? q[0] ? one(q[0].x) : zero(b) : (b = q.length, function(t) {
      for (var i2 = 0, o; i2 < b; ++i2)
        s[(o = q[i2]).i] = o.x(t);
      return s.join("");
    });
  }
  function interpolate$1(a, b) {
    var t = typeof b, c;
    return b == null || t === "boolean" ? constant$1(b) : (t === "number" ? interpolateNumber : t === "string" ? (c = color(b)) ? (b = c, interpolateRgb) : interpolateString : b instanceof color ? interpolateRgb : b instanceof Date ? date : isNumberArray(b) ? numberArray : Array.isArray(b) ? genericArray : typeof b.valueOf !== "function" && typeof b.toString !== "function" || isNaN(b) ? object : interpolateNumber)(a, b);
  }
  var degrees = 180 / Math.PI;
  var identity$1 = {
    translateX: 0,
    translateY: 0,
    rotate: 0,
    skewX: 0,
    scaleX: 1,
    scaleY: 1
  };
  function decompose(a, b, c, d, e, f) {
    var scaleX, scaleY, skewX;
    if (scaleX = Math.sqrt(a * a + b * b))
      a /= scaleX, b /= scaleX;
    if (skewX = a * c + b * d)
      c -= a * skewX, d -= b * skewX;
    if (scaleY = Math.sqrt(c * c + d * d))
      c /= scaleY, d /= scaleY, skewX /= scaleY;
    if (a * d < b * c)
      a = -a, b = -b, skewX = -skewX, scaleX = -scaleX;
    return {
      translateX: e,
      translateY: f,
      rotate: Math.atan2(b, a) * degrees,
      skewX: Math.atan(skewX) * degrees,
      scaleX,
      scaleY
    };
  }
  var svgNode;
  function parseCss(value) {
    const m = new (typeof DOMMatrix === "function" ? DOMMatrix : WebKitCSSMatrix)(value + "");
    return m.isIdentity ? identity$1 : decompose(m.a, m.b, m.c, m.d, m.e, m.f);
  }
  function parseSvg(value) {
    if (value == null)
      return identity$1;
    if (!svgNode)
      svgNode = document.createElementNS("http://www.w3.org/2000/svg", "g");
    svgNode.setAttribute("transform", value);
    if (!(value = svgNode.transform.baseVal.consolidate()))
      return identity$1;
    value = value.matrix;
    return decompose(value.a, value.b, value.c, value.d, value.e, value.f);
  }
  function interpolateTransform(parse, pxComma, pxParen, degParen) {
    function pop(s) {
      return s.length ? s.pop() + " " : "";
    }
    function translate(xa, ya, xb, yb, s, q) {
      if (xa !== xb || ya !== yb) {
        var i = s.push("translate(", null, pxComma, null, pxParen);
        q.push({ i: i - 4, x: interpolateNumber(xa, xb) }, { i: i - 2, x: interpolateNumber(ya, yb) });
      } else if (xb || yb) {
        s.push("translate(" + xb + pxComma + yb + pxParen);
      }
    }
    function rotate(a, b, s, q) {
      if (a !== b) {
        if (a - b > 180)
          b += 360;
        else if (b - a > 180)
          a += 360;
        q.push({ i: s.push(pop(s) + "rotate(", null, degParen) - 2, x: interpolateNumber(a, b) });
      } else if (b) {
        s.push(pop(s) + "rotate(" + b + degParen);
      }
    }
    function skewX(a, b, s, q) {
      if (a !== b) {
        q.push({ i: s.push(pop(s) + "skewX(", null, degParen) - 2, x: interpolateNumber(a, b) });
      } else if (b) {
        s.push(pop(s) + "skewX(" + b + degParen);
      }
    }
    function scale(xa, ya, xb, yb, s, q) {
      if (xa !== xb || ya !== yb) {
        var i = s.push(pop(s) + "scale(", null, ",", null, ")");
        q.push({ i: i - 4, x: interpolateNumber(xa, xb) }, { i: i - 2, x: interpolateNumber(ya, yb) });
      } else if (xb !== 1 || yb !== 1) {
        s.push(pop(s) + "scale(" + xb + "," + yb + ")");
      }
    }
    return function(a, b) {
      var s = [], q = [];
      a = parse(a), b = parse(b);
      translate(a.translateX, a.translateY, b.translateX, b.translateY, s, q);
      rotate(a.rotate, b.rotate, s, q);
      skewX(a.skewX, b.skewX, s, q);
      scale(a.scaleX, a.scaleY, b.scaleX, b.scaleY, s, q);
      a = b = null;
      return function(t) {
        var i = -1, n = q.length, o;
        while (++i < n)
          s[(o = q[i]).i] = o.x(t);
        return s.join("");
      };
    };
  }
  var interpolateTransformCss = interpolateTransform(parseCss, "px, ", "px)", "deg)");
  var interpolateTransformSvg = interpolateTransform(parseSvg, ", ", ")", ")");
  var epsilon2 = 1e-12;
  function cosh(x) {
    return ((x = Math.exp(x)) + 1 / x) / 2;
  }
  function sinh(x) {
    return ((x = Math.exp(x)) - 1 / x) / 2;
  }
  function tanh(x) {
    return ((x = Math.exp(2 * x)) - 1) / (x + 1);
  }
  var interpolateZoom = (function zoomRho(rho, rho2, rho4) {
    function zoom2(p0, p1) {
      var ux0 = p0[0], uy0 = p0[1], w0 = p0[2], ux1 = p1[0], uy1 = p1[1], w1 = p1[2], dx = ux1 - ux0, dy = uy1 - uy0, d2 = dx * dx + dy * dy, i, S;
      if (d2 < epsilon2) {
        S = Math.log(w1 / w0) / rho;
        i = function(t) {
          return [
            ux0 + t * dx,
            uy0 + t * dy,
            w0 * Math.exp(rho * t * S)
          ];
        };
      } else {
        var d1 = Math.sqrt(d2), b0 = (w1 * w1 - w0 * w0 + rho4 * d2) / (2 * w0 * rho2 * d1), b1 = (w1 * w1 - w0 * w0 - rho4 * d2) / (2 * w1 * rho2 * d1), r0 = Math.log(Math.sqrt(b0 * b0 + 1) - b0), r1 = Math.log(Math.sqrt(b1 * b1 + 1) - b1);
        S = (r1 - r0) / rho;
        i = function(t) {
          var s = t * S, coshr0 = cosh(r0), u = w0 / (rho2 * d1) * (coshr0 * tanh(rho * s + r0) - sinh(r0));
          return [
            ux0 + u * dx,
            uy0 + u * dy,
            w0 * coshr0 / cosh(rho * s + r0)
          ];
        };
      }
      i.duration = S * 1e3 * rho / Math.SQRT2;
      return i;
    }
    zoom2.rho = function(_) {
      var _1 = Math.max(1e-3, +_), _2 = _1 * _1, _4 = _2 * _2;
      return zoomRho(_1, _2, _4);
    };
    return zoom2;
  })(Math.SQRT2, 2, 4);
  var frame = 0;
  var timeout$1 = 0;
  var interval = 0;
  var pokeDelay = 1e3;
  var taskHead;
  var taskTail;
  var clockLast = 0;
  var clockNow = 0;
  var clockSkew = 0;
  var clock = typeof performance === "object" && performance.now ? performance : Date;
  var setFrame = typeof window === "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(f) {
    setTimeout(f, 17);
  };
  function now() {
    return clockNow || (setFrame(clearNow), clockNow = clock.now() + clockSkew);
  }
  function clearNow() {
    clockNow = 0;
  }
  function Timer() {
    this._call = this._time = this._next = null;
  }
  Timer.prototype = timer.prototype = {
    constructor: Timer,
    restart: function(callback, delay, time) {
      if (typeof callback !== "function")
        throw new TypeError("callback is not a function");
      time = (time == null ? now() : +time) + (delay == null ? 0 : +delay);
      if (!this._next && taskTail !== this) {
        if (taskTail)
          taskTail._next = this;
        else
          taskHead = this;
        taskTail = this;
      }
      this._call = callback;
      this._time = time;
      sleep();
    },
    stop: function() {
      if (this._call) {
        this._call = null;
        this._time = Infinity;
        sleep();
      }
    }
  };
  function timer(callback, delay, time) {
    var t = new Timer();
    t.restart(callback, delay, time);
    return t;
  }
  function timerFlush() {
    now();
    ++frame;
    var t = taskHead, e;
    while (t) {
      if ((e = clockNow - t._time) >= 0)
        t._call.call(void 0, e);
      t = t._next;
    }
    --frame;
  }
  function wake() {
    clockNow = (clockLast = clock.now()) + clockSkew;
    frame = timeout$1 = 0;
    try {
      timerFlush();
    } finally {
      frame = 0;
      nap();
      clockNow = 0;
    }
  }
  function poke() {
    var now2 = clock.now(), delay = now2 - clockLast;
    if (delay > pokeDelay)
      clockSkew -= delay, clockLast = now2;
  }
  function nap() {
    var t0, t1 = taskHead, t2, time = Infinity;
    while (t1) {
      if (t1._call) {
        if (time > t1._time)
          time = t1._time;
        t0 = t1, t1 = t1._next;
      } else {
        t2 = t1._next, t1._next = null;
        t1 = t0 ? t0._next = t2 : taskHead = t2;
      }
    }
    taskTail = t0;
    sleep(time);
  }
  function sleep(time) {
    if (frame)
      return;
    if (timeout$1)
      timeout$1 = clearTimeout(timeout$1);
    var delay = time - clockNow;
    if (delay > 24) {
      if (time < Infinity)
        timeout$1 = setTimeout(wake, time - clock.now() - clockSkew);
      if (interval)
        interval = clearInterval(interval);
    } else {
      if (!interval)
        clockLast = clock.now(), interval = setInterval(poke, pokeDelay);
      frame = 1, setFrame(wake);
    }
  }
  function timeout(callback, delay, time) {
    var t = new Timer();
    delay = delay == null ? 0 : +delay;
    t.restart((elapsed) => {
      t.stop();
      callback(elapsed + delay);
    }, delay, time);
    return t;
  }
  var emptyOn = dispatch("start", "end", "cancel", "interrupt");
  var emptyTween = [];
  var CREATED = 0;
  var SCHEDULED = 1;
  var STARTING = 2;
  var STARTED = 3;
  var RUNNING = 4;
  var ENDING = 5;
  var ENDED = 6;
  function schedule(node, name, id2, index, group, timing) {
    var schedules = node.__transition;
    if (!schedules)
      node.__transition = {};
    else if (id2 in schedules)
      return;
    create(node, id2, {
      name,
      index,
      // For context during callback.
      group,
      // For context during callback.
      on: emptyOn,
      tween: emptyTween,
      time: timing.time,
      delay: timing.delay,
      duration: timing.duration,
      ease: timing.ease,
      timer: null,
      state: CREATED
    });
  }
  function init(node, id2) {
    var schedule2 = get(node, id2);
    if (schedule2.state > CREATED)
      throw new Error("too late; already scheduled");
    return schedule2;
  }
  function set(node, id2) {
    var schedule2 = get(node, id2);
    if (schedule2.state > STARTED)
      throw new Error("too late; already running");
    return schedule2;
  }
  function get(node, id2) {
    var schedule2 = node.__transition;
    if (!schedule2 || !(schedule2 = schedule2[id2]))
      throw new Error("transition not found");
    return schedule2;
  }
  function create(node, id2, self2) {
    var schedules = node.__transition, tween;
    schedules[id2] = self2;
    self2.timer = timer(schedule2, 0, self2.time);
    function schedule2(elapsed) {
      self2.state = SCHEDULED;
      self2.timer.restart(start2, self2.delay, self2.time);
      if (self2.delay <= elapsed)
        start2(elapsed - self2.delay);
    }
    function start2(elapsed) {
      var i, j, n, o;
      if (self2.state !== SCHEDULED)
        return stop2();
      for (i in schedules) {
        o = schedules[i];
        if (o.name !== self2.name)
          continue;
        if (o.state === STARTED)
          return timeout(start2);
        if (o.state === RUNNING) {
          o.state = ENDED;
          o.timer.stop();
          o.on.call("interrupt", node, node.__data__, o.index, o.group);
          delete schedules[i];
        } else if (+i < id2) {
          o.state = ENDED;
          o.timer.stop();
          o.on.call("cancel", node, node.__data__, o.index, o.group);
          delete schedules[i];
        }
      }
      timeout(function() {
        if (self2.state === STARTED) {
          self2.state = RUNNING;
          self2.timer.restart(tick, self2.delay, self2.time);
          tick(elapsed);
        }
      });
      self2.state = STARTING;
      self2.on.call("start", node, node.__data__, self2.index, self2.group);
      if (self2.state !== STARTING)
        return;
      self2.state = STARTED;
      tween = new Array(n = self2.tween.length);
      for (i = 0, j = -1; i < n; ++i) {
        if (o = self2.tween[i].value.call(node, node.__data__, self2.index, self2.group)) {
          tween[++j] = o;
        }
      }
      tween.length = j + 1;
    }
    function tick(elapsed) {
      var t = elapsed < self2.duration ? self2.ease.call(null, elapsed / self2.duration) : (self2.timer.restart(stop2), self2.state = ENDING, 1), i = -1, n = tween.length;
      while (++i < n) {
        tween[i].call(node, t);
      }
      if (self2.state === ENDING) {
        self2.on.call("end", node, node.__data__, self2.index, self2.group);
        stop2();
      }
    }
    function stop2() {
      self2.state = ENDED;
      self2.timer.stop();
      delete schedules[id2];
      for (var i in schedules)
        return;
      delete node.__transition;
    }
  }
  function interrupt(node, name) {
    var schedules = node.__transition, schedule2, active, empty2 = true, i;
    if (!schedules)
      return;
    name = name == null ? null : name + "";
    for (i in schedules) {
      if ((schedule2 = schedules[i]).name !== name) {
        empty2 = false;
        continue;
      }
      active = schedule2.state > STARTING && schedule2.state < ENDING;
      schedule2.state = ENDED;
      schedule2.timer.stop();
      schedule2.on.call(active ? "interrupt" : "cancel", node, node.__data__, schedule2.index, schedule2.group);
      delete schedules[i];
    }
    if (empty2)
      delete node.__transition;
  }
  function selection_interrupt(name) {
    return this.each(function() {
      interrupt(this, name);
    });
  }
  function tweenRemove(id2, name) {
    var tween0, tween1;
    return function() {
      var schedule2 = set(this, id2), tween = schedule2.tween;
      if (tween !== tween0) {
        tween1 = tween0 = tween;
        for (var i = 0, n = tween1.length; i < n; ++i) {
          if (tween1[i].name === name) {
            tween1 = tween1.slice();
            tween1.splice(i, 1);
            break;
          }
        }
      }
      schedule2.tween = tween1;
    };
  }
  function tweenFunction(id2, name, value) {
    var tween0, tween1;
    if (typeof value !== "function")
      throw new Error();
    return function() {
      var schedule2 = set(this, id2), tween = schedule2.tween;
      if (tween !== tween0) {
        tween1 = (tween0 = tween).slice();
        for (var t = { name, value }, i = 0, n = tween1.length; i < n; ++i) {
          if (tween1[i].name === name) {
            tween1[i] = t;
            break;
          }
        }
        if (i === n)
          tween1.push(t);
      }
      schedule2.tween = tween1;
    };
  }
  function transition_tween(name, value) {
    var id2 = this._id;
    name += "";
    if (arguments.length < 2) {
      var tween = get(this.node(), id2).tween;
      for (var i = 0, n = tween.length, t; i < n; ++i) {
        if ((t = tween[i]).name === name) {
          return t.value;
        }
      }
      return null;
    }
    return this.each((value == null ? tweenRemove : tweenFunction)(id2, name, value));
  }
  function tweenValue(transition, name, value) {
    var id2 = transition._id;
    transition.each(function() {
      var schedule2 = set(this, id2);
      (schedule2.value || (schedule2.value = {}))[name] = value.apply(this, arguments);
    });
    return function(node) {
      return get(node, id2).value[name];
    };
  }
  function interpolate(a, b) {
    var c;
    return (typeof b === "number" ? interpolateNumber : b instanceof color ? interpolateRgb : (c = color(b)) ? (b = c, interpolateRgb) : interpolateString)(a, b);
  }
  function attrRemove(name) {
    return function() {
      this.removeAttribute(name);
    };
  }
  function attrRemoveNS(fullname) {
    return function() {
      this.removeAttributeNS(fullname.space, fullname.local);
    };
  }
  function attrConstant(name, interpolate2, value1) {
    var string00, string1 = value1 + "", interpolate0;
    return function() {
      var string0 = this.getAttribute(name);
      return string0 === string1 ? null : string0 === string00 ? interpolate0 : interpolate0 = interpolate2(string00 = string0, value1);
    };
  }
  function attrConstantNS(fullname, interpolate2, value1) {
    var string00, string1 = value1 + "", interpolate0;
    return function() {
      var string0 = this.getAttributeNS(fullname.space, fullname.local);
      return string0 === string1 ? null : string0 === string00 ? interpolate0 : interpolate0 = interpolate2(string00 = string0, value1);
    };
  }
  function attrFunction(name, interpolate2, value) {
    var string00, string10, interpolate0;
    return function() {
      var string0, value1 = value(this), string1;
      if (value1 == null)
        return void this.removeAttribute(name);
      string0 = this.getAttribute(name);
      string1 = value1 + "";
      return string0 === string1 ? null : string0 === string00 && string1 === string10 ? interpolate0 : (string10 = string1, interpolate0 = interpolate2(string00 = string0, value1));
    };
  }
  function attrFunctionNS(fullname, interpolate2, value) {
    var string00, string10, interpolate0;
    return function() {
      var string0, value1 = value(this), string1;
      if (value1 == null)
        return void this.removeAttributeNS(fullname.space, fullname.local);
      string0 = this.getAttributeNS(fullname.space, fullname.local);
      string1 = value1 + "";
      return string0 === string1 ? null : string0 === string00 && string1 === string10 ? interpolate0 : (string10 = string1, interpolate0 = interpolate2(string00 = string0, value1));
    };
  }
  function transition_attr(name, value) {
    var fullname = namespace(name), i = fullname === "transform" ? interpolateTransformSvg : interpolate;
    return this.attrTween(name, typeof value === "function" ? (fullname.local ? attrFunctionNS : attrFunction)(fullname, i, tweenValue(this, "attr." + name, value)) : value == null ? (fullname.local ? attrRemoveNS : attrRemove)(fullname) : (fullname.local ? attrConstantNS : attrConstant)(fullname, i, value));
  }
  function attrInterpolate(name, i) {
    return function(t) {
      this.setAttribute(name, i.call(this, t));
    };
  }
  function attrInterpolateNS(fullname, i) {
    return function(t) {
      this.setAttributeNS(fullname.space, fullname.local, i.call(this, t));
    };
  }
  function attrTweenNS(fullname, value) {
    var t0, i0;
    function tween() {
      var i = value.apply(this, arguments);
      if (i !== i0)
        t0 = (i0 = i) && attrInterpolateNS(fullname, i);
      return t0;
    }
    tween._value = value;
    return tween;
  }
  function attrTween(name, value) {
    var t0, i0;
    function tween() {
      var i = value.apply(this, arguments);
      if (i !== i0)
        t0 = (i0 = i) && attrInterpolate(name, i);
      return t0;
    }
    tween._value = value;
    return tween;
  }
  function transition_attrTween(name, value) {
    var key = "attr." + name;
    if (arguments.length < 2)
      return (key = this.tween(key)) && key._value;
    if (value == null)
      return this.tween(key, null);
    if (typeof value !== "function")
      throw new Error();
    var fullname = namespace(name);
    return this.tween(key, (fullname.local ? attrTweenNS : attrTween)(fullname, value));
  }
  function delayFunction(id2, value) {
    return function() {
      init(this, id2).delay = +value.apply(this, arguments);
    };
  }
  function delayConstant(id2, value) {
    return value = +value, function() {
      init(this, id2).delay = value;
    };
  }
  function transition_delay(value) {
    var id2 = this._id;
    return arguments.length ? this.each((typeof value === "function" ? delayFunction : delayConstant)(id2, value)) : get(this.node(), id2).delay;
  }
  function durationFunction(id2, value) {
    return function() {
      set(this, id2).duration = +value.apply(this, arguments);
    };
  }
  function durationConstant(id2, value) {
    return value = +value, function() {
      set(this, id2).duration = value;
    };
  }
  function transition_duration(value) {
    var id2 = this._id;
    return arguments.length ? this.each((typeof value === "function" ? durationFunction : durationConstant)(id2, value)) : get(this.node(), id2).duration;
  }
  function easeConstant(id2, value) {
    if (typeof value !== "function")
      throw new Error();
    return function() {
      set(this, id2).ease = value;
    };
  }
  function transition_ease(value) {
    var id2 = this._id;
    return arguments.length ? this.each(easeConstant(id2, value)) : get(this.node(), id2).ease;
  }
  function easeVarying(id2, value) {
    return function() {
      var v = value.apply(this, arguments);
      if (typeof v !== "function")
        throw new Error();
      set(this, id2).ease = v;
    };
  }
  function transition_easeVarying(value) {
    if (typeof value !== "function")
      throw new Error();
    return this.each(easeVarying(this._id, value));
  }
  function transition_filter(match) {
    if (typeof match !== "function")
      match = matcher(match);
    for (var groups = this._groups, m = groups.length, subgroups = new Array(m), j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, subgroup = subgroups[j] = [], node, i = 0; i < n; ++i) {
        if ((node = group[i]) && match.call(node, node.__data__, i, group)) {
          subgroup.push(node);
        }
      }
    }
    return new Transition(subgroups, this._parents, this._name, this._id);
  }
  function transition_merge(transition) {
    if (transition._id !== this._id)
      throw new Error();
    for (var groups0 = this._groups, groups1 = transition._groups, m0 = groups0.length, m1 = groups1.length, m = Math.min(m0, m1), merges = new Array(m0), j = 0; j < m; ++j) {
      for (var group0 = groups0[j], group1 = groups1[j], n = group0.length, merge = merges[j] = new Array(n), node, i = 0; i < n; ++i) {
        if (node = group0[i] || group1[i]) {
          merge[i] = node;
        }
      }
    }
    for (; j < m0; ++j) {
      merges[j] = groups0[j];
    }
    return new Transition(merges, this._parents, this._name, this._id);
  }
  function start(name) {
    return (name + "").trim().split(/^|\s+/).every(function(t) {
      var i = t.indexOf(".");
      if (i >= 0)
        t = t.slice(0, i);
      return !t || t === "start";
    });
  }
  function onFunction(id2, name, listener) {
    var on0, on1, sit = start(name) ? init : set;
    return function() {
      var schedule2 = sit(this, id2), on = schedule2.on;
      if (on !== on0)
        (on1 = (on0 = on).copy()).on(name, listener);
      schedule2.on = on1;
    };
  }
  function transition_on(name, listener) {
    var id2 = this._id;
    return arguments.length < 2 ? get(this.node(), id2).on.on(name) : this.each(onFunction(id2, name, listener));
  }
  function removeFunction(id2) {
    return function() {
      var parent = this.parentNode;
      for (var i in this.__transition)
        if (+i !== id2)
          return;
      if (parent)
        parent.removeChild(this);
    };
  }
  function transition_remove() {
    return this.on("end.remove", removeFunction(this._id));
  }
  function transition_select(select2) {
    var name = this._name, id2 = this._id;
    if (typeof select2 !== "function")
      select2 = selector(select2);
    for (var groups = this._groups, m = groups.length, subgroups = new Array(m), j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, subgroup = subgroups[j] = new Array(n), node, subnode, i = 0; i < n; ++i) {
        if ((node = group[i]) && (subnode = select2.call(node, node.__data__, i, group))) {
          if ("__data__" in node)
            subnode.__data__ = node.__data__;
          subgroup[i] = subnode;
          schedule(subgroup[i], name, id2, i, subgroup, get(node, id2));
        }
      }
    }
    return new Transition(subgroups, this._parents, name, id2);
  }
  function transition_selectAll(select2) {
    var name = this._name, id2 = this._id;
    if (typeof select2 !== "function")
      select2 = selectorAll(select2);
    for (var groups = this._groups, m = groups.length, subgroups = [], parents = [], j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, node, i = 0; i < n; ++i) {
        if (node = group[i]) {
          for (var children2 = select2.call(node, node.__data__, i, group), child, inherit2 = get(node, id2), k = 0, l = children2.length; k < l; ++k) {
            if (child = children2[k]) {
              schedule(child, name, id2, k, children2, inherit2);
            }
          }
          subgroups.push(children2);
          parents.push(node);
        }
      }
    }
    return new Transition(subgroups, parents, name, id2);
  }
  var Selection = selection.prototype.constructor;
  function transition_selection() {
    return new Selection(this._groups, this._parents);
  }
  function styleNull(name, interpolate2) {
    var string00, string10, interpolate0;
    return function() {
      var string0 = styleValue2(this, name), string1 = (this.style.removeProperty(name), styleValue2(this, name));
      return string0 === string1 ? null : string0 === string00 && string1 === string10 ? interpolate0 : interpolate0 = interpolate2(string00 = string0, string10 = string1);
    };
  }
  function styleRemove(name) {
    return function() {
      this.style.removeProperty(name);
    };
  }
  function styleConstant(name, interpolate2, value1) {
    var string00, string1 = value1 + "", interpolate0;
    return function() {
      var string0 = styleValue2(this, name);
      return string0 === string1 ? null : string0 === string00 ? interpolate0 : interpolate0 = interpolate2(string00 = string0, value1);
    };
  }
  function styleFunction(name, interpolate2, value) {
    var string00, string10, interpolate0;
    return function() {
      var string0 = styleValue2(this, name), value1 = value(this), string1 = value1 + "";
      if (value1 == null)
        string1 = value1 = (this.style.removeProperty(name), styleValue2(this, name));
      return string0 === string1 ? null : string0 === string00 && string1 === string10 ? interpolate0 : (string10 = string1, interpolate0 = interpolate2(string00 = string0, value1));
    };
  }
  function styleMaybeRemove(id2, name) {
    var on0, on1, listener0, key = "style." + name, event = "end." + key, remove22;
    return function() {
      var schedule2 = set(this, id2), on = schedule2.on, listener = schedule2.value[key] == null ? remove22 || (remove22 = styleRemove(name)) : void 0;
      if (on !== on0 || listener0 !== listener)
        (on1 = (on0 = on).copy()).on(event, listener0 = listener);
      schedule2.on = on1;
    };
  }
  function transition_style(name, value, priority) {
    var i = (name += "") === "transform" ? interpolateTransformCss : interpolate;
    return value == null ? this.styleTween(name, styleNull(name, i)).on("end.style." + name, styleRemove(name)) : typeof value === "function" ? this.styleTween(name, styleFunction(name, i, tweenValue(this, "style." + name, value))).each(styleMaybeRemove(this._id, name)) : this.styleTween(name, styleConstant(name, i, value), priority).on("end.style." + name, null);
  }
  function styleInterpolate(name, i, priority) {
    return function(t) {
      this.style.setProperty(name, i.call(this, t), priority);
    };
  }
  function styleTween(name, value, priority) {
    var t, i0;
    function tween() {
      var i = value.apply(this, arguments);
      if (i !== i0)
        t = (i0 = i) && styleInterpolate(name, i, priority);
      return t;
    }
    tween._value = value;
    return tween;
  }
  function transition_styleTween(name, value, priority) {
    var key = "style." + (name += "");
    if (arguments.length < 2)
      return (key = this.tween(key)) && key._value;
    if (value == null)
      return this.tween(key, null);
    if (typeof value !== "function")
      throw new Error();
    return this.tween(key, styleTween(name, value, priority == null ? "" : priority));
  }
  function textConstant(value) {
    return function() {
      this.textContent = value;
    };
  }
  function textFunction(value) {
    return function() {
      var value1 = value(this);
      this.textContent = value1 == null ? "" : value1;
    };
  }
  function transition_text(value) {
    return this.tween("text", typeof value === "function" ? textFunction(tweenValue(this, "text", value)) : textConstant(value == null ? "" : value + ""));
  }
  function textInterpolate(i) {
    return function(t) {
      this.textContent = i.call(this, t);
    };
  }
  function textTween(value) {
    var t0, i0;
    function tween() {
      var i = value.apply(this, arguments);
      if (i !== i0)
        t0 = (i0 = i) && textInterpolate(i);
      return t0;
    }
    tween._value = value;
    return tween;
  }
  function transition_textTween(value) {
    var key = "text";
    if (arguments.length < 1)
      return (key = this.tween(key)) && key._value;
    if (value == null)
      return this.tween(key, null);
    if (typeof value !== "function")
      throw new Error();
    return this.tween(key, textTween(value));
  }
  function transition_transition() {
    var name = this._name, id0 = this._id, id1 = newId();
    for (var groups = this._groups, m = groups.length, j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, node, i = 0; i < n; ++i) {
        if (node = group[i]) {
          var inherit2 = get(node, id0);
          schedule(node, name, id1, i, group, {
            time: inherit2.time + inherit2.delay + inherit2.duration,
            delay: 0,
            duration: inherit2.duration,
            ease: inherit2.ease
          });
        }
      }
    }
    return new Transition(groups, this._parents, name, id1);
  }
  function transition_end() {
    var on0, on1, that = this, id2 = that._id, size = that.size();
    return new Promise(function(resolve2, reject) {
      var cancel = { value: reject }, end = { value: function() {
        if (--size === 0)
          resolve2();
      } };
      that.each(function() {
        var schedule2 = set(this, id2), on = schedule2.on;
        if (on !== on0) {
          on1 = (on0 = on).copy();
          on1._.cancel.push(cancel);
          on1._.interrupt.push(cancel);
          on1._.end.push(end);
        }
        schedule2.on = on1;
      });
      if (size === 0)
        resolve2();
    });
  }
  var id = 0;
  function Transition(groups, parents, name, id2) {
    this._groups = groups;
    this._parents = parents;
    this._name = name;
    this._id = id2;
  }
  function newId() {
    return ++id;
  }
  var selection_prototype = selection.prototype;
  Transition.prototype = {
    constructor: Transition,
    select: transition_select,
    selectAll: transition_selectAll,
    selectChild: selection_prototype.selectChild,
    selectChildren: selection_prototype.selectChildren,
    filter: transition_filter,
    merge: transition_merge,
    selection: transition_selection,
    transition: transition_transition,
    call: selection_prototype.call,
    nodes: selection_prototype.nodes,
    node: selection_prototype.node,
    size: selection_prototype.size,
    empty: selection_prototype.empty,
    each: selection_prototype.each,
    on: transition_on,
    attr: transition_attr,
    attrTween: transition_attrTween,
    style: transition_style,
    styleTween: transition_styleTween,
    text: transition_text,
    textTween: transition_textTween,
    remove: transition_remove,
    tween: transition_tween,
    delay: transition_delay,
    duration: transition_duration,
    ease: transition_ease,
    easeVarying: transition_easeVarying,
    end: transition_end,
    [Symbol.iterator]: selection_prototype[Symbol.iterator]
  };
  function cubicInOut(t) {
    return ((t *= 2) <= 1 ? t * t * t : (t -= 2) * t * t + 2) / 2;
  }
  var defaultTiming = {
    time: null,
    // Set on use.
    delay: 0,
    duration: 250,
    ease: cubicInOut
  };
  function inherit(node, id2) {
    var timing;
    while (!(timing = node.__transition) || !(timing = timing[id2])) {
      if (!(node = node.parentNode)) {
        throw new Error(`transition ${id2} not found`);
      }
    }
    return timing;
  }
  function selection_transition(name) {
    var id2, timing;
    if (name instanceof Transition) {
      id2 = name._id, name = name._name;
    } else {
      id2 = newId(), (timing = defaultTiming).time = now(), name = name == null ? null : name + "";
    }
    for (var groups = this._groups, m = groups.length, j = 0; j < m; ++j) {
      for (var group = groups[j], n = group.length, node, i = 0; i < n; ++i) {
        if (node = group[i]) {
          schedule(node, name, id2, i, group, timing || inherit(node, id2));
        }
      }
    }
    return new Transition(groups, this._parents, name, id2);
  }
  selection.prototype.interrupt = selection_interrupt;
  selection.prototype.transition = selection_transition;
  var constant = (x) => () => x;
  function ZoomEvent(type, {
    sourceEvent: sourceEvent2,
    target,
    transform,
    dispatch: dispatch2
  }) {
    Object.defineProperties(this, {
      type: { value: type, enumerable: true, configurable: true },
      sourceEvent: { value: sourceEvent2, enumerable: true, configurable: true },
      target: { value: target, enumerable: true, configurable: true },
      transform: { value: transform, enumerable: true, configurable: true },
      _: { value: dispatch2 }
    });
  }
  function Transform(k, x, y) {
    this.k = k;
    this.x = x;
    this.y = y;
  }
  Transform.prototype = {
    constructor: Transform,
    scale: function(k) {
      return k === 1 ? this : new Transform(this.k * k, this.x, this.y);
    },
    translate: function(x, y) {
      return x === 0 & y === 0 ? this : new Transform(this.k, this.x + this.k * x, this.y + this.k * y);
    },
    apply: function(point) {
      return [point[0] * this.k + this.x, point[1] * this.k + this.y];
    },
    applyX: function(x) {
      return x * this.k + this.x;
    },
    applyY: function(y) {
      return y * this.k + this.y;
    },
    invert: function(location) {
      return [(location[0] - this.x) / this.k, (location[1] - this.y) / this.k];
    },
    invertX: function(x) {
      return (x - this.x) / this.k;
    },
    invertY: function(y) {
      return (y - this.y) / this.k;
    },
    rescaleX: function(x) {
      return x.copy().domain(x.range().map(this.invertX, this).map(x.invert, x));
    },
    rescaleY: function(y) {
      return y.copy().domain(y.range().map(this.invertY, this).map(y.invert, y));
    },
    toString: function() {
      return "translate(" + this.x + "," + this.y + ") scale(" + this.k + ")";
    }
  };
  var identity = new Transform(1, 0, 0);
  Transform.prototype;
  function nopropagation(event) {
    event.stopImmediatePropagation();
  }
  function noevent(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  function defaultFilter(event) {
    return (!event.ctrlKey || event.type === "wheel") && !event.button;
  }
  function defaultExtent() {
    var e = this;
    if (e instanceof SVGElement) {
      e = e.ownerSVGElement || e;
      if (e.hasAttribute("viewBox")) {
        e = e.viewBox.baseVal;
        return [[e.x, e.y], [e.x + e.width, e.y + e.height]];
      }
      return [[0, 0], [e.width.baseVal.value, e.height.baseVal.value]];
    }
    return [[0, 0], [e.clientWidth, e.clientHeight]];
  }
  function defaultTransform() {
    return this.__zoom || identity;
  }
  function defaultWheelDelta(event) {
    return -event.deltaY * (event.deltaMode === 1 ? 0.05 : event.deltaMode ? 1 : 2e-3) * (event.ctrlKey ? 10 : 1);
  }
  function defaultTouchable() {
    return navigator.maxTouchPoints || "ontouchstart" in this;
  }
  function defaultConstrain(transform, extent, translateExtent) {
    var dx0 = transform.invertX(extent[0][0]) - translateExtent[0][0], dx1 = transform.invertX(extent[1][0]) - translateExtent[1][0], dy0 = transform.invertY(extent[0][1]) - translateExtent[0][1], dy1 = transform.invertY(extent[1][1]) - translateExtent[1][1];
    return transform.translate(
      dx1 > dx0 ? (dx0 + dx1) / 2 : Math.min(0, dx0) || Math.max(0, dx1),
      dy1 > dy0 ? (dy0 + dy1) / 2 : Math.min(0, dy0) || Math.max(0, dy1)
    );
  }
  function zoom() {
    var filter2 = defaultFilter, extent = defaultExtent, constrain = defaultConstrain, wheelDelta2 = defaultWheelDelta, touchable = defaultTouchable, scaleExtent = [0, Infinity], translateExtent = [[-Infinity, -Infinity], [Infinity, Infinity]], duration = 250, interpolate2 = interpolateZoom, listeners = dispatch("start", "zoom", "end"), touchstarting, touchfirst, touchending, touchDelay = 500, wheelDelay = 150, clickDistance2 = 0, tapDistance = 10;
    function zoom2(selection2) {
      selection2.property("__zoom", defaultTransform).on("wheel.zoom", wheeled, { passive: false }).on("mousedown.zoom", mousedowned).on("dblclick.zoom", dblclicked).filter(touchable).on("touchstart.zoom", touchstarted).on("touchmove.zoom", touchmoved).on("touchend.zoom touchcancel.zoom", touchended).style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
    }
    zoom2.transform = function(collection, transform, point, event) {
      var selection2 = collection.selection ? collection.selection() : collection;
      selection2.property("__zoom", defaultTransform);
      if (collection !== selection2) {
        schedule2(collection, transform, point, event);
      } else {
        selection2.interrupt().each(function() {
          gesture(this, arguments).event(event).start().zoom(null, typeof transform === "function" ? transform.apply(this, arguments) : transform).end();
        });
      }
    };
    zoom2.scaleBy = function(selection2, k, p2, event) {
      zoom2.scaleTo(selection2, function() {
        var k0 = this.__zoom.k, k1 = typeof k === "function" ? k.apply(this, arguments) : k;
        return k0 * k1;
      }, p2, event);
    };
    zoom2.scaleTo = function(selection2, k, p2, event) {
      zoom2.transform(selection2, function() {
        var e = extent.apply(this, arguments), t0 = this.__zoom, p0 = p2 == null ? centroid(e) : typeof p2 === "function" ? p2.apply(this, arguments) : p2, p1 = t0.invert(p0), k1 = typeof k === "function" ? k.apply(this, arguments) : k;
        return constrain(translate(scale(t0, k1), p0, p1), e, translateExtent);
      }, p2, event);
    };
    zoom2.translateBy = function(selection2, x, y, event) {
      zoom2.transform(selection2, function() {
        return constrain(this.__zoom.translate(
          typeof x === "function" ? x.apply(this, arguments) : x,
          typeof y === "function" ? y.apply(this, arguments) : y
        ), extent.apply(this, arguments), translateExtent);
      }, null, event);
    };
    zoom2.translateTo = function(selection2, x, y, p2, event) {
      zoom2.transform(selection2, function() {
        var e = extent.apply(this, arguments), t = this.__zoom, p0 = p2 == null ? centroid(e) : typeof p2 === "function" ? p2.apply(this, arguments) : p2;
        return constrain(identity.translate(p0[0], p0[1]).scale(t.k).translate(
          typeof x === "function" ? -x.apply(this, arguments) : -x,
          typeof y === "function" ? -y.apply(this, arguments) : -y
        ), e, translateExtent);
      }, p2, event);
    };
    function scale(transform, k) {
      k = Math.max(scaleExtent[0], Math.min(scaleExtent[1], k));
      return k === transform.k ? transform : new Transform(k, transform.x, transform.y);
    }
    function translate(transform, p0, p1) {
      var x = p0[0] - p1[0] * transform.k, y = p0[1] - p1[1] * transform.k;
      return x === transform.x && y === transform.y ? transform : new Transform(transform.k, x, y);
    }
    function centroid(extent2) {
      return [(+extent2[0][0] + +extent2[1][0]) / 2, (+extent2[0][1] + +extent2[1][1]) / 2];
    }
    function schedule2(transition, transform, point, event) {
      transition.on("start.zoom", function() {
        gesture(this, arguments).event(event).start();
      }).on("interrupt.zoom end.zoom", function() {
        gesture(this, arguments).event(event).end();
      }).tween("zoom", function() {
        var that = this, args = arguments, g = gesture(that, args).event(event), e = extent.apply(that, args), p2 = point == null ? centroid(e) : typeof point === "function" ? point.apply(that, args) : point, w = Math.max(e[1][0] - e[0][0], e[1][1] - e[0][1]), a = that.__zoom, b = typeof transform === "function" ? transform.apply(that, args) : transform, i = interpolate2(a.invert(p2).concat(w / a.k), b.invert(p2).concat(w / b.k));
        return function(t) {
          if (t === 1)
            t = b;
          else {
            var l = i(t), k = w / l[2];
            t = new Transform(k, p2[0] - l[0] * k, p2[1] - l[1] * k);
          }
          g.zoom(null, t);
        };
      });
    }
    function gesture(that, args, clean) {
      return !clean && that.__zooming || new Gesture(that, args);
    }
    function Gesture(that, args) {
      this.that = that;
      this.args = args;
      this.active = 0;
      this.sourceEvent = null;
      this.extent = extent.apply(that, args);
      this.taps = 0;
    }
    Gesture.prototype = {
      event: function(event) {
        if (event)
          this.sourceEvent = event;
        return this;
      },
      start: function() {
        if (++this.active === 1) {
          this.that.__zooming = this;
          this.emit("start");
        }
        return this;
      },
      zoom: function(key, transform) {
        if (this.mouse && key !== "mouse")
          this.mouse[1] = transform.invert(this.mouse[0]);
        if (this.touch0 && key !== "touch")
          this.touch0[1] = transform.invert(this.touch0[0]);
        if (this.touch1 && key !== "touch")
          this.touch1[1] = transform.invert(this.touch1[0]);
        this.that.__zoom = transform;
        this.emit("zoom");
        return this;
      },
      end: function() {
        if (--this.active === 0) {
          delete this.that.__zooming;
          this.emit("end");
        }
        return this;
      },
      emit: function(type) {
        var d = select(this.that).datum();
        listeners.call(
          type,
          this.that,
          new ZoomEvent(type, {
            sourceEvent: this.sourceEvent,
            target: zoom2,
            type,
            transform: this.that.__zoom,
            dispatch: listeners
          }),
          d
        );
      }
    };
    function wheeled(event, ...args) {
      if (!filter2.apply(this, arguments))
        return;
      var g = gesture(this, args).event(event), t = this.__zoom, k = Math.max(scaleExtent[0], Math.min(scaleExtent[1], t.k * Math.pow(2, wheelDelta2.apply(this, arguments)))), p2 = pointer(event);
      if (g.wheel) {
        if (g.mouse[0][0] !== p2[0] || g.mouse[0][1] !== p2[1]) {
          g.mouse[1] = t.invert(g.mouse[0] = p2);
        }
        clearTimeout(g.wheel);
      } else if (t.k === k)
        return;
      else {
        g.mouse = [p2, t.invert(p2)];
        interrupt(this);
        g.start();
      }
      noevent(event);
      g.wheel = setTimeout(wheelidled, wheelDelay);
      g.zoom("mouse", constrain(translate(scale(t, k), g.mouse[0], g.mouse[1]), g.extent, translateExtent));
      function wheelidled() {
        g.wheel = null;
        g.end();
      }
    }
    function mousedowned(event, ...args) {
      if (touchending || !filter2.apply(this, arguments))
        return;
      var currentTarget = event.currentTarget, g = gesture(this, args, true).event(event), v = select(event.view).on("mousemove.zoom", mousemoved, true).on("mouseup.zoom", mouseupped, true), p2 = pointer(event, currentTarget), x0 = event.clientX, y0 = event.clientY;
      dragDisable(event.view);
      nopropagation(event);
      g.mouse = [p2, this.__zoom.invert(p2)];
      interrupt(this);
      g.start();
      function mousemoved(event2) {
        noevent(event2);
        if (!g.moved) {
          var dx = event2.clientX - x0, dy = event2.clientY - y0;
          g.moved = dx * dx + dy * dy > clickDistance2;
        }
        g.event(event2).zoom("mouse", constrain(translate(g.that.__zoom, g.mouse[0] = pointer(event2, currentTarget), g.mouse[1]), g.extent, translateExtent));
      }
      function mouseupped(event2) {
        v.on("mousemove.zoom mouseup.zoom", null);
        yesdrag(event2.view, g.moved);
        noevent(event2);
        g.event(event2).end();
      }
    }
    function dblclicked(event, ...args) {
      if (!filter2.apply(this, arguments))
        return;
      var t0 = this.__zoom, p0 = pointer(event.changedTouches ? event.changedTouches[0] : event, this), p1 = t0.invert(p0), k1 = t0.k * (event.shiftKey ? 0.5 : 2), t1 = constrain(translate(scale(t0, k1), p0, p1), extent.apply(this, args), translateExtent);
      noevent(event);
      if (duration > 0)
        select(this).transition().duration(duration).call(schedule2, t1, p0, event);
      else
        select(this).call(zoom2.transform, t1, p0, event);
    }
    function touchstarted(event, ...args) {
      if (!filter2.apply(this, arguments))
        return;
      var touches = event.touches, n = touches.length, g = gesture(this, args, event.changedTouches.length === n).event(event), started, i, t, p2;
      nopropagation(event);
      for (i = 0; i < n; ++i) {
        t = touches[i], p2 = pointer(t, this);
        p2 = [p2, this.__zoom.invert(p2), t.identifier];
        if (!g.touch0)
          g.touch0 = p2, started = true, g.taps = 1 + !!touchstarting;
        else if (!g.touch1 && g.touch0[2] !== p2[2])
          g.touch1 = p2, g.taps = 0;
      }
      if (touchstarting)
        touchstarting = clearTimeout(touchstarting);
      if (started) {
        if (g.taps < 2)
          touchfirst = p2[0], touchstarting = setTimeout(function() {
            touchstarting = null;
          }, touchDelay);
        interrupt(this);
        g.start();
      }
    }
    function touchmoved(event, ...args) {
      if (!this.__zooming)
        return;
      var g = gesture(this, args).event(event), touches = event.changedTouches, n = touches.length, i, t, p2, l;
      noevent(event);
      for (i = 0; i < n; ++i) {
        t = touches[i], p2 = pointer(t, this);
        if (g.touch0 && g.touch0[2] === t.identifier)
          g.touch0[0] = p2;
        else if (g.touch1 && g.touch1[2] === t.identifier)
          g.touch1[0] = p2;
      }
      t = g.that.__zoom;
      if (g.touch1) {
        var p0 = g.touch0[0], l0 = g.touch0[1], p1 = g.touch1[0], l1 = g.touch1[1], dp = (dp = p1[0] - p0[0]) * dp + (dp = p1[1] - p0[1]) * dp, dl = (dl = l1[0] - l0[0]) * dl + (dl = l1[1] - l0[1]) * dl;
        t = scale(t, Math.sqrt(dp / dl));
        p2 = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2];
        l = [(l0[0] + l1[0]) / 2, (l0[1] + l1[1]) / 2];
      } else if (g.touch0)
        p2 = g.touch0[0], l = g.touch0[1];
      else
        return;
      g.zoom("touch", constrain(translate(t, p2, l), g.extent, translateExtent));
    }
    function touchended(event, ...args) {
      if (!this.__zooming)
        return;
      var g = gesture(this, args).event(event), touches = event.changedTouches, n = touches.length, i, t;
      nopropagation(event);
      if (touchending)
        clearTimeout(touchending);
      touchending = setTimeout(function() {
        touchending = null;
      }, touchDelay);
      for (i = 0; i < n; ++i) {
        t = touches[i];
        if (g.touch0 && g.touch0[2] === t.identifier)
          delete g.touch0;
        else if (g.touch1 && g.touch1[2] === t.identifier)
          delete g.touch1;
      }
      if (g.touch1 && !g.touch0)
        g.touch0 = g.touch1, delete g.touch1;
      if (g.touch0)
        g.touch0[1] = this.__zoom.invert(g.touch0[0]);
      else {
        g.end();
        if (g.taps === 2) {
          t = pointer(t, this);
          if (Math.hypot(touchfirst[0] - t[0], touchfirst[1] - t[1]) < tapDistance) {
            var p2 = select(this).on("dblclick.zoom");
            if (p2)
              p2.apply(this, arguments);
          }
        }
      }
    }
    zoom2.wheelDelta = function(_) {
      return arguments.length ? (wheelDelta2 = typeof _ === "function" ? _ : constant(+_), zoom2) : wheelDelta2;
    };
    zoom2.filter = function(_) {
      return arguments.length ? (filter2 = typeof _ === "function" ? _ : constant(!!_), zoom2) : filter2;
    };
    zoom2.touchable = function(_) {
      return arguments.length ? (touchable = typeof _ === "function" ? _ : constant(!!_), zoom2) : touchable;
    };
    zoom2.extent = function(_) {
      return arguments.length ? (extent = typeof _ === "function" ? _ : constant([[+_[0][0], +_[0][1]], [+_[1][0], +_[1][1]]]), zoom2) : extent;
    };
    zoom2.scaleExtent = function(_) {
      return arguments.length ? (scaleExtent[0] = +_[0], scaleExtent[1] = +_[1], zoom2) : [scaleExtent[0], scaleExtent[1]];
    };
    zoom2.translateExtent = function(_) {
      return arguments.length ? (translateExtent[0][0] = +_[0][0], translateExtent[1][0] = +_[1][0], translateExtent[0][1] = +_[0][1], translateExtent[1][1] = +_[1][1], zoom2) : [[translateExtent[0][0], translateExtent[0][1]], [translateExtent[1][0], translateExtent[1][1]]];
    };
    zoom2.constrain = function(_) {
      return arguments.length ? (constrain = _, zoom2) : constrain;
    };
    zoom2.duration = function(_) {
      return arguments.length ? (duration = +_, zoom2) : duration;
    };
    zoom2.interpolate = function(_) {
      return arguments.length ? (interpolate2 = _, zoom2) : interpolate2;
    };
    zoom2.on = function() {
      var value = listeners.on.apply(listeners, arguments);
      return value === listeners ? zoom2 : value;
    };
    zoom2.clickDistance = function(_) {
      return arguments.length ? (clickDistance2 = (_ = +_) * _, zoom2) : Math.sqrt(clickDistance2);
    };
    zoom2.tapDistance = function(_) {
      return arguments.length ? (tapDistance = +_, zoom2) : tapDistance;
    };
    return zoom2;
  }
  var Position = /* @__PURE__ */ ((Position2) => {
    Position2["Left"] = "left";
    Position2["Top"] = "top";
    Position2["Right"] = "right";
    Position2["Bottom"] = "bottom";
    return Position2;
  })(Position || {});
  var SelectionMode = /* @__PURE__ */ ((SelectionMode2) => {
    SelectionMode2["Partial"] = "partial";
    SelectionMode2["Full"] = "full";
    return SelectionMode2;
  })(SelectionMode || {});
  var ConnectionLineType = /* @__PURE__ */ ((ConnectionLineType2) => {
    ConnectionLineType2["Bezier"] = "default";
    ConnectionLineType2["SimpleBezier"] = "simple-bezier";
    ConnectionLineType2["Straight"] = "straight";
    ConnectionLineType2["Step"] = "step";
    ConnectionLineType2["SmoothStep"] = "smoothstep";
    return ConnectionLineType2;
  })(ConnectionLineType || {});
  var ConnectionMode = /* @__PURE__ */ ((ConnectionMode2) => {
    ConnectionMode2["Strict"] = "strict";
    ConnectionMode2["Loose"] = "loose";
    return ConnectionMode2;
  })(ConnectionMode || {});
  var MarkerType = /* @__PURE__ */ ((MarkerType2) => {
    MarkerType2["Arrow"] = "arrow";
    MarkerType2["ArrowClosed"] = "arrowclosed";
    return MarkerType2;
  })(MarkerType || {});
  var PanOnScrollMode = /* @__PURE__ */ ((PanOnScrollMode2) => {
    PanOnScrollMode2["Free"] = "free";
    PanOnScrollMode2["Vertical"] = "vertical";
    PanOnScrollMode2["Horizontal"] = "horizontal";
    return PanOnScrollMode2;
  })(PanOnScrollMode || {});
  var PanelPosition = /* @__PURE__ */ ((PanelPosition2) => {
    PanelPosition2["TopLeft"] = "top-left";
    PanelPosition2["TopCenter"] = "top-center";
    PanelPosition2["TopRight"] = "top-right";
    PanelPosition2["BottomLeft"] = "bottom-left";
    PanelPosition2["BottomCenter"] = "bottom-center";
    PanelPosition2["BottomRight"] = "bottom-right";
    return PanelPosition2;
  })(PanelPosition || {});
  var inputTags = ["INPUT", "SELECT", "TEXTAREA"];
  var defaultDoc = typeof document !== "undefined" ? document : null;
  function isInputDOMNode(event) {
    var _a, _b;
    const target = ((_b = (_a = event.composedPath) == null ? void 0 : _a.call(event)) == null ? void 0 : _b[0]) || event.target;
    const hasAttribute = typeof (target == null ? void 0 : target.hasAttribute) === "function" ? target.hasAttribute("contenteditable") : false;
    const closest = typeof (target == null ? void 0 : target.closest) === "function" ? target.closest(".nokey") : null;
    return inputTags.includes(target == null ? void 0 : target.nodeName) || hasAttribute || !!closest;
  }
  function wasModifierPressed(event) {
    return event.ctrlKey || event.metaKey || event.shiftKey || event.altKey;
  }
  function isKeyMatch(pressedKey, keyToMatch, pressedKeys, isKeyUp) {
    const keyCombination = keyToMatch.replace("+", "\n").replace("\n\n", "\n+").split("\n").map((k) => k.trim().toLowerCase());
    if (keyCombination.length === 1) {
      return pressedKey.toLowerCase() === keyToMatch.toLowerCase();
    }
    if (!isKeyUp) {
      pressedKeys.add(pressedKey.toLowerCase());
    }
    const isMatch = keyCombination.every(
      (key, index) => pressedKeys.has(key) && Array.from(pressedKeys.values())[index] === keyCombination[index]
    );
    if (isKeyUp) {
      pressedKeys.delete(pressedKey.toLowerCase());
    }
    return isMatch;
  }
  function createKeyPredicate(keyFilter, pressedKeys) {
    return (event) => {
      if (!event.code && !event.key) {
        return false;
      }
      const keyOrCode = useKeyOrCode(event.code, keyFilter);
      if (Array.isArray(keyFilter)) {
        return keyFilter.some((key) => isKeyMatch(event[keyOrCode], key, pressedKeys, event.type === "keyup"));
      }
      return isKeyMatch(event[keyOrCode], keyFilter, pressedKeys, event.type === "keyup");
    };
  }
  function useKeyOrCode(code, keysToWatch) {
    return keysToWatch.includes(code) ? "code" : "key";
  }
  function useKeyPress(keyFilter, options) {
    const target = computed2(() => toValue(options == null ? void 0 : options.target) ?? defaultDoc);
    const isPressed = shallowRef(toValue(keyFilter) === true);
    let modifierPressed = false;
    const pressedKeys = /* @__PURE__ */ new Set();
    let currentFilter = createKeyFilterFn(toValue(keyFilter));
    watch2(
      () => toValue(keyFilter),
      (nextKeyFilter, previousKeyFilter) => {
        if (typeof previousKeyFilter === "boolean" && typeof nextKeyFilter !== "boolean") {
          reset();
        }
        currentFilter = createKeyFilterFn(nextKeyFilter);
      },
      {
        immediate: true
      }
    );
    useEventListener(["blur", "contextmenu"], reset);
    onKeyStroke(
      (...args) => currentFilter(...args),
      (e) => {
        var _a, _b;
        const actInsideInputWithModifier = toValue(options == null ? void 0 : options.actInsideInputWithModifier) ?? true;
        const preventDefault = toValue(options == null ? void 0 : options.preventDefault) ?? false;
        modifierPressed = wasModifierPressed(e);
        const preventAction = (!modifierPressed || modifierPressed && !actInsideInputWithModifier) && isInputDOMNode(e);
        if (preventAction) {
          return;
        }
        const target2 = ((_b = (_a = e.composedPath) == null ? void 0 : _a.call(e)) == null ? void 0 : _b[0]) || e.target;
        const isInteractiveElement = (target2 == null ? void 0 : target2.nodeName) === "BUTTON" || (target2 == null ? void 0 : target2.nodeName) === "A";
        if (!preventDefault && (modifierPressed || !isInteractiveElement)) {
          e.preventDefault();
        }
        isPressed.value = true;
      },
      { eventName: "keydown", target }
    );
    onKeyStroke(
      (...args) => currentFilter(...args),
      (e) => {
        const actInsideInputWithModifier = toValue(options == null ? void 0 : options.actInsideInputWithModifier) ?? true;
        if (isPressed.value) {
          const preventAction = (!modifierPressed || modifierPressed && !actInsideInputWithModifier) && isInputDOMNode(e);
          if (preventAction) {
            return;
          }
          modifierPressed = false;
          isPressed.value = false;
        }
      },
      { eventName: "keyup", target }
    );
    function reset() {
      modifierPressed = false;
      pressedKeys.clear();
      isPressed.value = toValue(keyFilter) === true;
    }
    function createKeyFilterFn(keyFilter2) {
      if (keyFilter2 === null) {
        reset();
        return () => false;
      }
      if (typeof keyFilter2 === "boolean") {
        reset();
        isPressed.value = keyFilter2;
        return () => false;
      }
      if (Array.isArray(keyFilter2) || typeof keyFilter2 === "string") {
        return createKeyPredicate(keyFilter2, pressedKeys);
      }
      return keyFilter2;
    }
    return isPressed;
  }
  var ARIA_NODE_DESC_KEY = "vue-flow__node-desc";
  var ARIA_EDGE_DESC_KEY = "vue-flow__edge-desc";
  var ARIA_LIVE_MESSAGE = "vue-flow__aria-live";
  var elementSelectionKeys = ["Enter", " ", "Escape"];
  var arrowKeyDiffs = {
    ArrowUp: { x: 0, y: -1 },
    ArrowDown: { x: 0, y: 1 },
    ArrowLeft: { x: -1, y: 0 },
    ArrowRight: { x: 1, y: 0 }
  };
  function nodeToRect(node) {
    return {
      ...node.computedPosition || { x: 0, y: 0 },
      width: node.dimensions.width || 0,
      height: node.dimensions.height || 0
    };
  }
  function getOverlappingArea(rectA, rectB) {
    const xOverlap = Math.max(0, Math.min(rectA.x + rectA.width, rectB.x + rectB.width) - Math.max(rectA.x, rectB.x));
    const yOverlap = Math.max(0, Math.min(rectA.y + rectA.height, rectB.y + rectB.height) - Math.max(rectA.y, rectB.y));
    return Math.ceil(xOverlap * yOverlap);
  }
  function getDimensions(node) {
    return {
      width: node.offsetWidth,
      height: node.offsetHeight
    };
  }
  function clamp(val, min = 0, max = 1) {
    return Math.min(Math.max(val, min), max);
  }
  function clampPosition(position, extent) {
    return {
      x: clamp(position.x, extent[0][0], extent[1][0]),
      y: clamp(position.y, extent[0][1], extent[1][1])
    };
  }
  function getHostForElement(element) {
    const doc2 = element.getRootNode();
    if ("elementFromPoint" in doc2) {
      return doc2;
    }
    return window.document;
  }
  function isEdge(element) {
    return element && typeof element === "object" && "id" in element && "source" in element && "target" in element;
  }
  function isNode(element) {
    return element && typeof element === "object" && "id" in element && "position" in element && !isEdge(element);
  }
  function isGraphNode(element) {
    return isNode(element) && "computedPosition" in element;
  }
  function isNumeric(n) {
    return !Number.isNaN(n) && Number.isFinite(n);
  }
  function isRect(obj) {
    return isNumeric(obj.width) && isNumeric(obj.height) && isNumeric(obj.x) && isNumeric(obj.y);
  }
  function parseNode(node, existingNode, parentNode) {
    const initialState2 = {
      id: node.id.toString(),
      type: node.type ?? "default",
      dimensions: markRaw({
        width: 0,
        height: 0
      }),
      computedPosition: markRaw({
        z: 0,
        ...node.position
      }),
      // todo: shouldn't be defined initially, as we want to use handleBounds to check if a node was actually initialized or not
      handleBounds: {
        source: [],
        target: []
      },
      draggable: void 0,
      selectable: void 0,
      connectable: void 0,
      focusable: void 0,
      selected: false,
      dragging: false,
      resizing: false,
      initialized: false,
      isParent: false,
      position: {
        x: 0,
        y: 0
      },
      data: isDef(node.data) ? node.data : {},
      events: markRaw(isDef(node.events) ? node.events : {})
    };
    return Object.assign(existingNode ?? initialState2, node, { id: node.id.toString(), parentNode });
  }
  function parseEdge(edge, existingEdge, defaultEdgeOptions) {
    var _a, _b;
    const initialState2 = {
      id: edge.id.toString(),
      type: edge.type ?? (existingEdge == null ? void 0 : existingEdge.type) ?? "default",
      source: edge.source.toString(),
      target: edge.target.toString(),
      sourceHandle: (_a = edge.sourceHandle) == null ? void 0 : _a.toString(),
      targetHandle: (_b = edge.targetHandle) == null ? void 0 : _b.toString(),
      updatable: edge.updatable ?? (defaultEdgeOptions == null ? void 0 : defaultEdgeOptions.updatable),
      selectable: edge.selectable ?? (defaultEdgeOptions == null ? void 0 : defaultEdgeOptions.selectable),
      focusable: edge.focusable ?? (defaultEdgeOptions == null ? void 0 : defaultEdgeOptions.focusable),
      data: isDef(edge.data) ? edge.data : {},
      events: markRaw(isDef(edge.events) ? edge.events : {}),
      label: edge.label ?? "",
      interactionWidth: edge.interactionWidth ?? (defaultEdgeOptions == null ? void 0 : defaultEdgeOptions.interactionWidth),
      ...defaultEdgeOptions ?? {}
    };
    return Object.assign(existingEdge ?? initialState2, edge, { id: edge.id.toString() });
  }
  function getConnectedElements(nodeOrId, nodes, edges, dir) {
    const id2 = typeof nodeOrId === "string" ? nodeOrId : nodeOrId.id;
    const connectedIds = /* @__PURE__ */ new Set();
    const origin = dir === "source" ? "target" : "source";
    for (const edge of edges) {
      if (edge[origin] === id2) {
        connectedIds.add(edge[dir]);
      }
    }
    return nodes.filter((n) => connectedIds.has(n.id));
  }
  function getOutgoers(...args) {
    if (args.length === 3) {
      const [nodeOrId2, nodes, edges] = args;
      return getConnectedElements(nodeOrId2, nodes, edges, "target");
    }
    const [nodeOrId, elements] = args;
    const nodeId = typeof nodeOrId === "string" ? nodeOrId : nodeOrId.id;
    const outgoers = elements.filter((el) => isEdge(el) && el.source === nodeId);
    return outgoers.map((edge) => elements.find((el) => isNode(el) && el.id === edge.target));
  }
  function getIncomers(...args) {
    if (args.length === 3) {
      const [nodeOrId2, nodes, edges] = args;
      return getConnectedElements(nodeOrId2, nodes, edges, "source");
    }
    const [nodeOrId, elements] = args;
    const nodeId = typeof nodeOrId === "string" ? nodeOrId : nodeOrId.id;
    const incomers = elements.filter((el) => isEdge(el) && el.target === nodeId);
    return incomers.map((edge) => elements.find((el) => isNode(el) && el.id === edge.source));
  }
  function getEdgeId({ source, sourceHandle, target, targetHandle }) {
    return `vueflow__edge-${source}${sourceHandle ?? ""}-${target}${targetHandle ?? ""}`;
  }
  function connectionExists(edge, elements) {
    return elements.some(
      (el) => isEdge(el) && el.source === edge.source && el.target === edge.target && (el.sourceHandle === edge.sourceHandle || !el.sourceHandle && !edge.sourceHandle) && (el.targetHandle === edge.targetHandle || !el.targetHandle && !edge.targetHandle)
    );
  }
  function rendererPointToPoint({ x, y }, { x: tx, y: ty, zoom: tScale }) {
    return {
      x: x * tScale + tx,
      y: y * tScale + ty
    };
  }
  function pointToRendererPoint({ x, y }, { x: tx, y: ty, zoom: tScale }, snapToGrid = false, snapGrid = [1, 1]) {
    const position = {
      x: (x - tx) / tScale,
      y: (y - ty) / tScale
    };
    return snapToGrid ? snapPosition(position, snapGrid) : position;
  }
  function getBoundsOfBoxes(box1, box2) {
    return {
      x: Math.min(box1.x, box2.x),
      y: Math.min(box1.y, box2.y),
      x2: Math.max(box1.x2, box2.x2),
      y2: Math.max(box1.y2, box2.y2)
    };
  }
  function rectToBox({ x, y, width, height }) {
    return {
      x,
      y,
      x2: x + width,
      y2: y + height
    };
  }
  function boxToRect({ x, y, x2, y2 }) {
    return {
      x,
      y,
      width: x2 - x,
      height: y2 - y
    };
  }
  function getRectOfNodes(nodes) {
    let box = {
      x: Number.POSITIVE_INFINITY,
      y: Number.POSITIVE_INFINITY,
      x2: Number.NEGATIVE_INFINITY,
      y2: Number.NEGATIVE_INFINITY
    };
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      box = getBoundsOfBoxes(
        box,
        rectToBox({
          ...node.computedPosition,
          ...node.dimensions
        })
      );
    }
    return boxToRect(box);
  }
  function getNodesInside(nodes, rect, viewport = { x: 0, y: 0, zoom: 1 }, partially = false, excludeNonSelectableNodes = false) {
    const paneRect = {
      ...pointToRendererPoint(rect, viewport),
      width: rect.width / viewport.zoom,
      height: rect.height / viewport.zoom
    };
    const visibleNodes = [];
    for (const node of nodes) {
      const { dimensions, selectable = true, hidden = false } = node;
      const width = dimensions.width ?? node.width ?? null;
      const height = dimensions.height ?? node.height ?? null;
      if (excludeNonSelectableNodes && !selectable || hidden) {
        continue;
      }
      const overlappingArea = getOverlappingArea(paneRect, nodeToRect(node));
      const notInitialized = width === null || height === null;
      const partiallyVisible = partially && overlappingArea > 0;
      const area = (width ?? 0) * (height ?? 0);
      const isVisible = notInitialized || partiallyVisible || overlappingArea >= area;
      if (isVisible || node.dragging) {
        visibleNodes.push(node);
      }
    }
    return visibleNodes;
  }
  function getConnectedEdges(nodesOrId, edges) {
    const nodeIds = /* @__PURE__ */ new Set();
    if (typeof nodesOrId === "string") {
      nodeIds.add(nodesOrId);
    } else if (nodesOrId.length >= 1) {
      for (const n of nodesOrId) {
        nodeIds.add(n.id);
      }
    }
    return edges.filter((edge) => nodeIds.has(edge.source) || nodeIds.has(edge.target));
  }
  function parsePadding(padding, viewport) {
    if (typeof padding === "number") {
      return Math.floor((viewport - viewport / (1 + padding)) * 0.5);
    }
    if (typeof padding === "string" && padding.endsWith("px")) {
      const paddingValue = Number.parseFloat(padding);
      if (!Number.isNaN(paddingValue)) {
        return Math.floor(paddingValue);
      }
    }
    if (typeof padding === "string" && padding.endsWith("%")) {
      const paddingValue = Number.parseFloat(padding);
      if (!Number.isNaN(paddingValue)) {
        return Math.floor(viewport * paddingValue * 0.01);
      }
    }
    warn3(`The padding value "${padding}" is invalid. Please provide a number or a string with a valid unit (px or %).`);
    return 0;
  }
  function parsePaddings(padding, width, height) {
    if (typeof padding === "string" || typeof padding === "number") {
      const paddingY = parsePadding(padding, height);
      const paddingX = parsePadding(padding, width);
      return {
        top: paddingY,
        right: paddingX,
        bottom: paddingY,
        left: paddingX,
        x: paddingX * 2,
        y: paddingY * 2
      };
    }
    if (typeof padding === "object") {
      const top = parsePadding(padding.top ?? padding.y ?? 0, height);
      const bottom = parsePadding(padding.bottom ?? padding.y ?? 0, height);
      const left = parsePadding(padding.left ?? padding.x ?? 0, width);
      const right = parsePadding(padding.right ?? padding.x ?? 0, width);
      return { top, right, bottom, left, x: left + right, y: top + bottom };
    }
    return { top: 0, right: 0, bottom: 0, left: 0, x: 0, y: 0 };
  }
  function calculateAppliedPaddings(bounds, x, y, zoom2, width, height) {
    const { x: left, y: top } = rendererPointToPoint(bounds, { x, y, zoom: zoom2 });
    const { x: boundRight, y: boundBottom } = rendererPointToPoint(
      { x: bounds.x + bounds.width, y: bounds.y + bounds.height },
      {
        x,
        y,
        zoom: zoom2
      }
    );
    const right = width - boundRight;
    const bottom = height - boundBottom;
    return {
      left: Math.floor(left),
      top: Math.floor(top),
      right: Math.floor(right),
      bottom: Math.floor(bottom)
    };
  }
  function getTransformForBounds(bounds, width, height, minZoom, maxZoom, padding = 0.1) {
    const p2 = parsePaddings(padding, width, height);
    const xZoom = (width - p2.x) / bounds.width;
    const yZoom = (height - p2.y) / bounds.height;
    const zoom2 = Math.min(xZoom, yZoom);
    const clampedZoom = clamp(zoom2, minZoom, maxZoom);
    const boundsCenterX = bounds.x + bounds.width / 2;
    const boundsCenterY = bounds.y + bounds.height / 2;
    const x = width / 2 - boundsCenterX * clampedZoom;
    const y = height / 2 - boundsCenterY * clampedZoom;
    const newPadding = calculateAppliedPaddings(bounds, x, y, clampedZoom, width, height);
    const offset = {
      left: Math.min(newPadding.left - p2.left, 0),
      top: Math.min(newPadding.top - p2.top, 0),
      right: Math.min(newPadding.right - p2.right, 0),
      bottom: Math.min(newPadding.bottom - p2.bottom, 0)
    };
    return {
      x: x - offset.left + offset.right,
      y: y - offset.top + offset.bottom,
      zoom: clampedZoom
    };
  }
  function getXYZPos(parentPos, computedPosition) {
    return {
      x: computedPosition.x + parentPos.x,
      y: computedPosition.y + parentPos.y,
      z: (parentPos.z > computedPosition.z ? parentPos.z : computedPosition.z) + 1
    };
  }
  function isParentSelected(node, nodeLookup) {
    if (!node.parentNode) {
      return false;
    }
    const parent = nodeLookup.get(node.parentNode);
    if (!parent) {
      return false;
    }
    if (parent.selected) {
      return true;
    }
    return isParentSelected(parent, nodeLookup);
  }
  function getMarkerId(marker, vueFlowId) {
    if (typeof marker === "undefined") {
      return "";
    }
    if (typeof marker === "string") {
      return marker;
    }
    const idPrefix = vueFlowId ? `${vueFlowId}__` : "";
    return `${idPrefix}${Object.keys(marker).sort().map((key) => `${key}=${marker[key]}`).join("&")}`;
  }
  function wheelDelta(event) {
    const factor = event.ctrlKey && isMacOs() ? 10 : 1;
    return -event.deltaY * (event.deltaMode === 1 ? 0.05 : event.deltaMode ? 1 : 2e-3) * factor;
  }
  function calcAutoPanVelocity(value, min, max) {
    if (value < min) {
      return clamp(Math.abs(value - min), 1, min) / min;
    }
    if (value > max) {
      return -clamp(Math.abs(value - max), 1, min) / min;
    }
    return 0;
  }
  function calcAutoPan(pos, bounds, speed = 15, distance2 = 40) {
    const xMovement = calcAutoPanVelocity(pos.x, distance2, bounds.width - distance2) * speed;
    const yMovement = calcAutoPanVelocity(pos.y, distance2, bounds.height - distance2) * speed;
    return [xMovement, yMovement];
  }
  function handleParentExpand(updateItem, parent) {
    if (parent) {
      const extendWidth = updateItem.position.x + updateItem.dimensions.width - parent.dimensions.width;
      const extendHeight = updateItem.position.y + updateItem.dimensions.height - parent.dimensions.height;
      if (extendWidth > 0 || extendHeight > 0 || updateItem.position.x < 0 || updateItem.position.y < 0) {
        let parentStyles = {};
        if (typeof parent.style === "function") {
          parentStyles = { ...parent.style(parent) };
        } else if (parent.style) {
          parentStyles = { ...parent.style };
        }
        parentStyles.width = parentStyles.width ?? `${parent.dimensions.width}px`;
        parentStyles.height = parentStyles.height ?? `${parent.dimensions.height}px`;
        if (extendWidth > 0) {
          if (typeof parentStyles.width === "string") {
            const currWidth = Number(parentStyles.width.replace("px", ""));
            parentStyles.width = `${currWidth + extendWidth}px`;
          } else {
            parentStyles.width += extendWidth;
          }
        }
        if (extendHeight > 0) {
          if (typeof parentStyles.height === "string") {
            const currWidth = Number(parentStyles.height.replace("px", ""));
            parentStyles.height = `${currWidth + extendHeight}px`;
          } else {
            parentStyles.height += extendHeight;
          }
        }
        if (updateItem.position.x < 0) {
          const xDiff = Math.abs(updateItem.position.x);
          parent.position.x = parent.position.x - xDiff;
          if (typeof parentStyles.width === "string") {
            const currWidth = Number(parentStyles.width.replace("px", ""));
            parentStyles.width = `${currWidth + xDiff}px`;
          } else {
            parentStyles.width += xDiff;
          }
          updateItem.position.x = 0;
        }
        if (updateItem.position.y < 0) {
          const yDiff = Math.abs(updateItem.position.y);
          parent.position.y = parent.position.y - yDiff;
          if (typeof parentStyles.height === "string") {
            const currWidth = Number(parentStyles.height.replace("px", ""));
            parentStyles.height = `${currWidth + yDiff}px`;
          } else {
            parentStyles.height += yDiff;
          }
          updateItem.position.y = 0;
        }
        parent.dimensions.width = Number(parentStyles.width.toString().replace("px", ""));
        parent.dimensions.height = Number(parentStyles.height.toString().replace("px", ""));
        if (typeof parent.style === "function") {
          parent.style = (p2) => {
            const styleFunc = parent.style;
            return {
              ...styleFunc(p2),
              ...parentStyles
            };
          };
        } else {
          parent.style = {
            ...parent.style,
            ...parentStyles
          };
        }
      }
    }
  }
  function applyChanges(changes, elements) {
    var _a, _b;
    const addRemoveChanges = changes.filter((c) => c.type === "add" || c.type === "remove");
    for (const change of addRemoveChanges) {
      if (change.type === "add") {
        const index = elements.findIndex((el) => el.id === change.item.id);
        if (index === -1) {
          elements.push(change.item);
        }
      } else if (change.type === "remove") {
        const index = elements.findIndex((el) => el.id === change.id);
        if (index !== -1) {
          elements.splice(index, 1);
        }
      }
    }
    const elementIds = elements.map((el) => el.id);
    for (const element of elements) {
      for (const currentChange of changes) {
        if (currentChange.id !== element.id) {
          continue;
        }
        switch (currentChange.type) {
          case "select":
            element.selected = currentChange.selected;
            break;
          case "position":
            if (isGraphNode(element)) {
              if (typeof currentChange.position !== "undefined") {
                element.position = currentChange.position;
              }
              if (typeof currentChange.dragging !== "undefined") {
                element.dragging = currentChange.dragging;
              }
              if (element.expandParent && element.parentNode) {
                const parent = elements[elementIds.indexOf(element.parentNode)];
                if (parent && isGraphNode(parent)) {
                  handleParentExpand(element, parent);
                }
              }
            }
            break;
          case "dimensions":
            if (isGraphNode(element)) {
              if (typeof currentChange.dimensions !== "undefined") {
                element.dimensions = currentChange.dimensions;
              }
              if (typeof currentChange.updateStyle !== "undefined" && currentChange.updateStyle) {
                element.style = {
                  ...element.style || {},
                  width: `${(_a = currentChange.dimensions) == null ? void 0 : _a.width}px`,
                  height: `${(_b = currentChange.dimensions) == null ? void 0 : _b.height}px`
                };
              }
              if (typeof currentChange.resizing !== "undefined") {
                element.resizing = currentChange.resizing;
              }
              if (element.expandParent && element.parentNode) {
                const parent = elements[elementIds.indexOf(element.parentNode)];
                if (parent && isGraphNode(parent)) {
                  const parentInit = !!parent.dimensions.width && !!parent.dimensions.height;
                  if (!parentInit) {
                    nextTick(() => {
                      handleParentExpand(element, parent);
                    });
                  } else {
                    handleParentExpand(element, parent);
                  }
                }
              }
            }
            break;
        }
      }
    }
    return elements;
  }
  function applyEdgeChanges(changes, edges) {
    return applyChanges(changes, edges);
  }
  function applyNodeChanges(changes, nodes) {
    return applyChanges(changes, nodes);
  }
  function createSelectionChange(id2, selected) {
    return {
      id: id2,
      type: "select",
      selected
    };
  }
  function createAdditionChange(item) {
    return {
      item,
      type: "add"
    };
  }
  function createNodeRemoveChange(id2) {
    return {
      id: id2,
      type: "remove"
    };
  }
  function createEdgeRemoveChange(id2, source, target, sourceHandle, targetHandle) {
    return {
      id: id2,
      source,
      target,
      sourceHandle: sourceHandle || null,
      targetHandle: targetHandle || null,
      type: "remove"
    };
  }
  function getSelectionChanges(items, selectedIds = /* @__PURE__ */ new Set(), mutateItem = false) {
    const changes = [];
    for (const [id2, item] of items) {
      const willBeSelected = selectedIds.has(id2);
      if (!(item.selected === void 0 && !willBeSelected) && item.selected !== willBeSelected) {
        if (mutateItem) {
          item.selected = willBeSelected;
        }
        changes.push(createSelectionChange(item.id, willBeSelected));
      }
    }
    return changes;
  }
  var noop$1 = () => {
  };
  function createExtendedEventHook(defaultHandler) {
    const listeners = /* @__PURE__ */ new Set();
    let emitter = noop$1;
    let hasEmitListeners = () => false;
    const hasListeners = () => listeners.size > 0 || hasEmitListeners();
    const setEmitter = (fn) => {
      emitter = fn;
    };
    const removeEmitter = () => {
      emitter = noop$1;
    };
    const setHasEmitListeners = (fn) => {
      hasEmitListeners = fn;
    };
    const removeHasEmitListeners = () => {
      hasEmitListeners = () => false;
    };
    const off = (fn) => {
      listeners.delete(fn);
    };
    const on = (fn) => {
      listeners.add(fn);
      const offFn = () => off(fn);
      tryOnScopeDispose(offFn);
      return { off: offFn };
    };
    const trigger2 = (param) => {
      const queue2 = [emitter];
      if (hasListeners()) {
        queue2.push(...listeners);
      } else if (defaultHandler) {
        queue2.push(defaultHandler);
      }
      return Promise.allSettled(queue2.map((fn) => fn(param)));
    };
    return {
      on,
      off,
      trigger: trigger2,
      hasListeners,
      listeners,
      setEmitter,
      removeEmitter,
      setHasEmitListeners,
      removeHasEmitListeners
    };
  }
  function hasSelector(target, selector2, node) {
    let current = target;
    do {
      if (current && current.matches(selector2)) {
        return true;
      } else if (current === node) {
        return false;
      }
      current = current.parentElement;
    } while (current);
    return false;
  }
  function getDragItems(nodeLookup, nodesDraggable, mousePos, nodeId) {
    var _a, _b;
    const dragItems = /* @__PURE__ */ new Map();
    for (const [id2, node] of nodeLookup) {
      if ((node.selected || node.id === nodeId) && (!node.parentNode || !isParentSelected(node, nodeLookup)) && (node.draggable || nodesDraggable && typeof node.draggable === "undefined")) {
        const internalNode = nodeLookup.get(id2);
        if (internalNode) {
          dragItems.set(id2, {
            id: node.id,
            position: node.position || { x: 0, y: 0 },
            distance: {
              x: mousePos.x - ((_a = node.computedPosition) == null ? void 0 : _a.x) || 0,
              y: mousePos.y - ((_b = node.computedPosition) == null ? void 0 : _b.y) || 0
            },
            from: { x: node.computedPosition.x, y: node.computedPosition.y },
            extent: node.extent,
            parentNode: node.parentNode,
            dimensions: { ...node.dimensions },
            expandParent: node.expandParent
          });
        }
      }
    }
    return Array.from(dragItems.values());
  }
  function getEventHandlerParams({
    id: id2,
    dragItems,
    findNode
  }) {
    const extendedDragItems = [];
    for (const dragItem of dragItems) {
      const node = findNode(dragItem.id);
      if (node) {
        extendedDragItems.push(node);
      }
    }
    return [id2 ? extendedDragItems.find((n) => n.id === id2) : extendedDragItems[0], extendedDragItems];
  }
  function getExtentPadding(padding) {
    if (Array.isArray(padding)) {
      switch (padding.length) {
        case 1:
          return [padding[0], padding[0], padding[0], padding[0]];
        case 2:
          return [padding[0], padding[1], padding[0], padding[1]];
        case 3:
          return [padding[0], padding[1], padding[2], padding[1]];
        case 4:
          return padding;
        default:
          return [0, 0, 0, 0];
      }
    }
    return [padding, padding, padding, padding];
  }
  function getParentExtent(currentExtent, node, parent) {
    const [top, right, bottom, left] = typeof currentExtent !== "string" ? getExtentPadding(currentExtent.padding) : [0, 0, 0, 0];
    if (parent && typeof parent.computedPosition.x !== "undefined" && typeof parent.computedPosition.y !== "undefined" && typeof parent.dimensions.width !== "undefined" && typeof parent.dimensions.height !== "undefined") {
      return [
        [parent.computedPosition.x + left, parent.computedPosition.y + top],
        [
          parent.computedPosition.x + parent.dimensions.width - right,
          parent.computedPosition.y + parent.dimensions.height - bottom
        ]
      ];
    }
    return false;
  }
  function getExtent(item, triggerError, extent, parent) {
    let currentExtent = item.extent || extent;
    if ((currentExtent === "parent" || !Array.isArray(currentExtent) && (currentExtent == null ? void 0 : currentExtent.range) === "parent") && !item.expandParent) {
      if (item.parentNode && parent && item.dimensions.width && item.dimensions.height) {
        const parentExtent = getParentExtent(currentExtent, item, parent);
        if (parentExtent) {
          currentExtent = parentExtent;
        }
      } else {
        triggerError(new VueFlowError(ErrorCode.NODE_EXTENT_INVALID, item.id));
        currentExtent = extent;
      }
    } else if (Array.isArray(currentExtent)) {
      const parentX = (parent == null ? void 0 : parent.computedPosition.x) || 0;
      const parentY = (parent == null ? void 0 : parent.computedPosition.y) || 0;
      currentExtent = [
        [currentExtent[0][0] + parentX, currentExtent[0][1] + parentY],
        [currentExtent[1][0] + parentX, currentExtent[1][1] + parentY]
      ];
    } else if (currentExtent !== "parent" && (currentExtent == null ? void 0 : currentExtent.range) && Array.isArray(currentExtent.range)) {
      const [top, right, bottom, left] = getExtentPadding(currentExtent.padding);
      const parentX = (parent == null ? void 0 : parent.computedPosition.x) || 0;
      const parentY = (parent == null ? void 0 : parent.computedPosition.y) || 0;
      currentExtent = [
        [currentExtent.range[0][0] + parentX + left, currentExtent.range[0][1] + parentY + top],
        [currentExtent.range[1][0] + parentX - right, currentExtent.range[1][1] + parentY - bottom]
      ];
    }
    return currentExtent === "parent" ? [
      [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
      [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY]
    ] : currentExtent;
  }
  function clampNodeExtent({ width, height }, extent) {
    return [extent[0], [extent[1][0] - (width || 0), extent[1][1] - (height || 0)]];
  }
  function calcNextPosition(node, nextPosition, triggerError, nodeExtent, parentNode) {
    const extent = clampNodeExtent(node.dimensions, getExtent(node, triggerError, nodeExtent, parentNode));
    const clampedPos = clampPosition(nextPosition, extent);
    return {
      position: {
        x: clampedPos.x - ((parentNode == null ? void 0 : parentNode.computedPosition.x) || 0),
        y: clampedPos.y - ((parentNode == null ? void 0 : parentNode.computedPosition.y) || 0)
      },
      computedPosition: clampedPos
    };
  }
  function getHandlePosition(node, handle, fallbackPosition = Position.Left, center = false) {
    const x = ((handle == null ? void 0 : handle.x) ?? 0) + node.computedPosition.x;
    const y = ((handle == null ? void 0 : handle.y) ?? 0) + node.computedPosition.y;
    const { width, height } = handle ?? getNodeDimensions(node);
    if (center) {
      return { x: x + width / 2, y: y + height / 2 };
    }
    const position = (handle == null ? void 0 : handle.position) ?? fallbackPosition;
    switch (position) {
      case Position.Top:
        return { x: x + width / 2, y };
      case Position.Right:
        return { x: x + width, y: y + height / 2 };
      case Position.Bottom:
        return { x: x + width / 2, y: y + height };
      case Position.Left:
        return { x, y: y + height / 2 };
    }
  }
  function getEdgeHandle(bounds, handleId) {
    if (!bounds) {
      return null;
    }
    return (!handleId ? bounds[0] : bounds.find((d) => d.id === handleId)) || null;
  }
  function isEdgeVisible({
    sourcePos,
    targetPos,
    sourceWidth,
    sourceHeight,
    targetWidth,
    targetHeight,
    width,
    height,
    viewport
  }) {
    const edgeBox = {
      x: Math.min(sourcePos.x, targetPos.x),
      y: Math.min(sourcePos.y, targetPos.y),
      x2: Math.max(sourcePos.x + sourceWidth, targetPos.x + targetWidth),
      y2: Math.max(sourcePos.y + sourceHeight, targetPos.y + targetHeight)
    };
    if (edgeBox.x === edgeBox.x2) {
      edgeBox.x2 += 1;
    }
    if (edgeBox.y === edgeBox.y2) {
      edgeBox.y2 += 1;
    }
    const viewBox = rectToBox({
      x: (0 - viewport.x) / viewport.zoom,
      y: (0 - viewport.y) / viewport.zoom,
      width: width / viewport.zoom,
      height: height / viewport.zoom
    });
    const xOverlap = Math.max(0, Math.min(viewBox.x2, edgeBox.x2) - Math.max(viewBox.x, edgeBox.x));
    const yOverlap = Math.max(0, Math.min(viewBox.y2, edgeBox.y2) - Math.max(viewBox.y, edgeBox.y));
    const overlappingArea = Math.ceil(xOverlap * yOverlap);
    return overlappingArea > 0;
  }
  function getEdgeZIndex(edge, findNode, elevateEdgesOnSelect = false) {
    const hasZIndex = typeof edge.zIndex === "number";
    let z = hasZIndex ? edge.zIndex : 0;
    const source = findNode(edge.source);
    const target = findNode(edge.target);
    if (!source || !target) {
      return 0;
    }
    if (elevateEdgesOnSelect) {
      z = hasZIndex ? edge.zIndex : Math.max(source.computedPosition.z || 0, target.computedPosition.z || 0);
    }
    return z;
  }
  var ErrorCode = /* @__PURE__ */ ((ErrorCode2) => {
    ErrorCode2["MISSING_STYLES"] = "MISSING_STYLES";
    ErrorCode2["MISSING_VIEWPORT_DIMENSIONS"] = "MISSING_VIEWPORT_DIMENSIONS";
    ErrorCode2["NODE_INVALID"] = "NODE_INVALID";
    ErrorCode2["NODE_NOT_FOUND"] = "NODE_NOT_FOUND";
    ErrorCode2["NODE_MISSING_PARENT"] = "NODE_MISSING_PARENT";
    ErrorCode2["NODE_TYPE_MISSING"] = "NODE_TYPE_MISSING";
    ErrorCode2["NODE_EXTENT_INVALID"] = "NODE_EXTENT_INVALID";
    ErrorCode2["EDGE_INVALID"] = "EDGE_INVALID";
    ErrorCode2["EDGE_NOT_FOUND"] = "EDGE_NOT_FOUND";
    ErrorCode2["EDGE_SOURCE_MISSING"] = "EDGE_SOURCE_MISSING";
    ErrorCode2["EDGE_TARGET_MISSING"] = "EDGE_TARGET_MISSING";
    ErrorCode2["EDGE_TYPE_MISSING"] = "EDGE_TYPE_MISSING";
    ErrorCode2["EDGE_SOURCE_TARGET_SAME"] = "EDGE_SOURCE_TARGET_SAME";
    ErrorCode2["EDGE_SOURCE_TARGET_MISSING"] = "EDGE_SOURCE_TARGET_MISSING";
    ErrorCode2["EDGE_ORPHANED"] = "EDGE_ORPHANED";
    ErrorCode2["USEVUEFLOW_OPTIONS"] = "USEVUEFLOW_OPTIONS";
    return ErrorCode2;
  })(ErrorCode || {});
  var messages = {
    [
      "MISSING_STYLES"
      /* MISSING_STYLES */
    ]: () => `It seems that you haven't loaded the necessary styles. Please import '@vue-flow/core/dist/style.css' to ensure that the graph is rendered correctly`,
    [
      "MISSING_VIEWPORT_DIMENSIONS"
      /* MISSING_VIEWPORT_DIMENSIONS */
    ]: () => "The Vue Flow parent container needs a width and a height to render the graph",
    [
      "NODE_INVALID"
      /* NODE_INVALID */
    ]: (id2) => `Node is invalid
Node: ${id2}`,
    [
      "NODE_NOT_FOUND"
      /* NODE_NOT_FOUND */
    ]: (id2) => `Node not found
Node: ${id2}`,
    [
      "NODE_MISSING_PARENT"
      /* NODE_MISSING_PARENT */
    ]: (id2, parentId) => `Node is missing a parent
Node: ${id2}
Parent: ${parentId}`,
    [
      "NODE_TYPE_MISSING"
      /* NODE_TYPE_MISSING */
    ]: (type) => `Node type is missing
Type: ${type}`,
    [
      "NODE_EXTENT_INVALID"
      /* NODE_EXTENT_INVALID */
    ]: (id2) => `Only child nodes can use a parent extent
Node: ${id2}`,
    [
      "EDGE_INVALID"
      /* EDGE_INVALID */
    ]: (id2) => `An edge needs a source and a target
Edge: ${id2}`,
    [
      "EDGE_SOURCE_MISSING"
      /* EDGE_SOURCE_MISSING */
    ]: (id2, source) => `Edge source is missing
Edge: ${id2} 
Source: ${source}`,
    [
      "EDGE_TARGET_MISSING"
      /* EDGE_TARGET_MISSING */
    ]: (id2, target) => `Edge target is missing
Edge: ${id2} 
Target: ${target}`,
    [
      "EDGE_TYPE_MISSING"
      /* EDGE_TYPE_MISSING */
    ]: (type) => `Edge type is missing
Type: ${type}`,
    [
      "EDGE_SOURCE_TARGET_SAME"
      /* EDGE_SOURCE_TARGET_SAME */
    ]: (id2, source, target) => `Edge source and target are the same
Edge: ${id2} 
Source: ${source} 
Target: ${target}`,
    [
      "EDGE_SOURCE_TARGET_MISSING"
      /* EDGE_SOURCE_TARGET_MISSING */
    ]: (id2, source, target) => `Edge source or target is missing
Edge: ${id2} 
Source: ${source} 
Target: ${target}`,
    [
      "EDGE_ORPHANED"
      /* EDGE_ORPHANED */
    ]: (id2) => `Edge was orphaned (suddenly missing source or target) and has been removed
Edge: ${id2}`,
    [
      "EDGE_NOT_FOUND"
      /* EDGE_NOT_FOUND */
    ]: (id2) => `Edge not found
Edge: ${id2}`,
    // deprecation errors
    [
      "USEVUEFLOW_OPTIONS"
      /* USEVUEFLOW_OPTIONS */
    ]: () => `The options parameter is deprecated and will be removed in the next major version. Please use the id parameter instead`
  };
  var VueFlowError = class extends Error {
    constructor(code, ...args) {
      var _a;
      super((_a = messages[code]) == null ? void 0 : _a.call(messages, ...args));
      this.name = "VueFlowError";
      this.code = code;
      this.args = args;
    }
  };
  function isMouseEvent(event) {
    return "clientX" in event;
  }
  function isUseDragEvent(event) {
    return "sourceEvent" in event;
  }
  function getEventPosition(event, bounds) {
    const isMouse = isMouseEvent(event);
    let evtX;
    let evtY;
    if (isMouse) {
      evtX = event.clientX;
      evtY = event.clientY;
    } else if ("touches" in event && event.touches.length > 0) {
      evtX = event.touches[0].clientX;
      evtY = event.touches[0].clientY;
    } else if ("changedTouches" in event && event.changedTouches.length > 0) {
      evtX = event.changedTouches[0].clientX;
      evtY = event.changedTouches[0].clientY;
    } else {
      evtX = 0;
      evtY = 0;
    }
    return {
      x: evtX - ((bounds == null ? void 0 : bounds.left) ?? 0),
      y: evtY - ((bounds == null ? void 0 : bounds.top) ?? 0)
    };
  }
  var isMacOs = () => {
    var _a;
    return typeof navigator !== "undefined" && ((_a = navigator == null ? void 0 : navigator.userAgent) == null ? void 0 : _a.indexOf("Mac")) >= 0;
  };
  function getNodeDimensions(node) {
    var _a, _b;
    return {
      width: ((_a = node.dimensions) == null ? void 0 : _a.width) ?? node.width ?? 0,
      height: ((_b = node.dimensions) == null ? void 0 : _b.height) ?? node.height ?? 0
    };
  }
  function snapPosition(position, snapGrid = [1, 1]) {
    return {
      x: snapGrid[0] * Math.round(position.x / snapGrid[0]),
      y: snapGrid[1] * Math.round(position.y / snapGrid[1])
    };
  }
  var alwaysValid$1 = () => true;
  function resetRecentHandle(handleDomNode) {
    handleDomNode == null ? void 0 : handleDomNode.classList.remove("valid", "connecting", "vue-flow__handle-valid", "vue-flow__handle-connecting");
  }
  function getNodesWithinDistance(position, nodeLookup, distance2) {
    const nodes = [];
    const rect = {
      x: position.x - distance2,
      y: position.y - distance2,
      width: distance2 * 2,
      height: distance2 * 2
    };
    for (const node of nodeLookup.values()) {
      if (getOverlappingArea(rect, nodeToRect(node)) > 0) {
        nodes.push(node);
      }
    }
    return nodes;
  }
  var ADDITIONAL_DISTANCE = 250;
  function getClosestHandle(position, connectionRadius, nodeLookup, fromHandle) {
    var _a, _b;
    let closestHandles = [];
    let minDistance = Number.POSITIVE_INFINITY;
    const closeNodes = getNodesWithinDistance(position, nodeLookup, connectionRadius + ADDITIONAL_DISTANCE);
    for (const node of closeNodes) {
      const allHandles = [...((_a = node.handleBounds) == null ? void 0 : _a.source) ?? [], ...((_b = node.handleBounds) == null ? void 0 : _b.target) ?? []];
      for (const handle of allHandles) {
        if (fromHandle.nodeId === handle.nodeId && fromHandle.type === handle.type && fromHandle.id === handle.id) {
          continue;
        }
        const { x, y } = getHandlePosition(node, handle, handle.position, true);
        const distance2 = Math.sqrt((x - position.x) ** 2 + (y - position.y) ** 2);
        if (distance2 > connectionRadius) {
          continue;
        }
        if (distance2 < minDistance) {
          closestHandles = [{ ...handle, x, y }];
          minDistance = distance2;
        } else if (distance2 === minDistance) {
          closestHandles.push({ ...handle, x, y });
        }
      }
    }
    if (!closestHandles.length) {
      return null;
    }
    if (closestHandles.length > 1) {
      const oppositeHandleType = fromHandle.type === "source" ? "target" : "source";
      return closestHandles.find((handle) => handle.type === oppositeHandleType) ?? closestHandles[0];
    }
    return closestHandles[0];
  }
  function isValidHandle(event, {
    handle,
    connectionMode,
    fromNodeId,
    fromHandleId,
    fromType,
    doc: doc2,
    lib,
    flowId,
    isValidConnection = alwaysValid$1
  }, edges, nodes, findNode, nodeLookup) {
    const isTarget = fromType === "target";
    const handleDomNode = handle ? doc2.querySelector(`.${lib}-flow__handle[data-id="${flowId}-${handle == null ? void 0 : handle.nodeId}-${handle == null ? void 0 : handle.id}-${handle == null ? void 0 : handle.type}"]`) : null;
    const { x, y } = getEventPosition(event);
    const handleBelow = doc2.elementFromPoint(x, y);
    const handleToCheck = (handleBelow == null ? void 0 : handleBelow.classList.contains(`${lib}-flow__handle`)) ? handleBelow : handleDomNode;
    const result = {
      handleDomNode: handleToCheck,
      isValid: false,
      connection: null,
      toHandle: null
    };
    if (handleToCheck) {
      const handleType = getHandleType(void 0, handleToCheck);
      const handleNodeId = handleToCheck.getAttribute("data-nodeid");
      const handleId = handleToCheck.getAttribute("data-handleid");
      const connectable = handleToCheck.classList.contains("connectable");
      const connectableEnd = handleToCheck.classList.contains("connectableend");
      if (!handleNodeId || !handleType) {
        return result;
      }
      const connection = {
        source: isTarget ? handleNodeId : fromNodeId,
        sourceHandle: isTarget ? handleId : fromHandleId,
        target: isTarget ? fromNodeId : handleNodeId,
        targetHandle: isTarget ? fromHandleId : handleId
      };
      result.connection = connection;
      const isConnectable = connectable && connectableEnd;
      const isValid = isConnectable && (connectionMode === ConnectionMode.Strict ? isTarget && handleType === "source" || !isTarget && handleType === "target" : handleNodeId !== fromNodeId || handleId !== fromHandleId);
      result.isValid = isValid && isValidConnection(connection, {
        nodes,
        edges,
        sourceNode: findNode(connection.source),
        targetNode: findNode(connection.target)
      });
      result.toHandle = getHandle(handleNodeId, handleType, handleId, nodeLookup, connectionMode, true);
    }
    return result;
  }
  function getHandleType(edgeUpdaterType, handleDomNode) {
    if (edgeUpdaterType) {
      return edgeUpdaterType;
    } else if (handleDomNode == null ? void 0 : handleDomNode.classList.contains("target")) {
      return "target";
    } else if (handleDomNode == null ? void 0 : handleDomNode.classList.contains("source")) {
      return "source";
    }
    return null;
  }
  function getConnectionStatus(isInsideConnectionRadius, isHandleValid) {
    let connectionStatus = null;
    if (isHandleValid) {
      connectionStatus = "valid";
    } else if (isInsideConnectionRadius && !isHandleValid) {
      connectionStatus = "invalid";
    }
    return connectionStatus;
  }
  function isConnectionValid(isInsideConnectionRadius, isHandleValid) {
    let isValid = null;
    if (isHandleValid) {
      isValid = true;
    } else if (isInsideConnectionRadius && !isHandleValid) {
      isValid = false;
    }
    return isValid;
  }
  function getHandle(nodeId, handleType, handleId, nodeLookup, connectionMode, withAbsolutePosition = false) {
    var _a, _b, _c;
    const node = nodeLookup.get(nodeId);
    if (!node) {
      return null;
    }
    const handles = connectionMode === ConnectionMode.Strict ? (_a = node.handleBounds) == null ? void 0 : _a[handleType] : [...((_b = node.handleBounds) == null ? void 0 : _b.source) ?? [], ...((_c = node.handleBounds) == null ? void 0 : _c.target) ?? []];
    const handle = (handleId ? handles == null ? void 0 : handles.find((h2) => h2.id === handleId) : handles == null ? void 0 : handles[0]) ?? null;
    return handle && withAbsolutePosition ? { ...handle, ...getHandlePosition(node, handle, handle.position, true) } : handle;
  }
  var oppositePosition = {
    [Position.Left]: Position.Right,
    [Position.Right]: Position.Left,
    [Position.Top]: Position.Bottom,
    [Position.Bottom]: Position.Top
  };
  var productionEnvs = ["production", "prod"];
  function warn3(message, ...args) {
    if (isDev()) {
      console.warn(`[Vue Flow]: ${message}`, ...args);
    }
  }
  function isDev() {
    return !productionEnvs.includes("development");
  }
  function getHandleBounds(type, nodeElement, nodeBounds, zoom2, nodeId) {
    const handles = nodeElement.querySelectorAll(`.vue-flow__handle.${type}`);
    if (!(handles == null ? void 0 : handles.length)) {
      return null;
    }
    return Array.from(handles).map((handle) => {
      const handleBounds = handle.getBoundingClientRect();
      return {
        id: handle.getAttribute("data-handleid"),
        type,
        nodeId,
        position: handle.getAttribute("data-handlepos"),
        x: (handleBounds.left - nodeBounds.left) / zoom2,
        y: (handleBounds.top - nodeBounds.top) / zoom2,
        ...getDimensions(handle)
      };
    });
  }
  function handleNodeClick(node, multiSelectionActive, addSelectedNodes, removeSelectedNodes, nodesSelectionActive, unselect = false, nodeEl) {
    nodesSelectionActive.value = false;
    if (!node.selected) {
      addSelectedNodes([node]);
    } else if (unselect || node.selected && multiSelectionActive) {
      removeSelectedNodes([node]);
      nextTick(() => {
        nodeEl.blur();
      });
    }
  }
  function isDef(val) {
    const unrefVal = unref(val);
    return typeof unrefVal !== "undefined";
  }
  function addEdgeToStore(edgeParams, edges, triggerError, defaultEdgeOptions) {
    if (!edgeParams || !edgeParams.source || !edgeParams.target) {
      triggerError(new VueFlowError(ErrorCode.EDGE_INVALID, (edgeParams == null ? void 0 : edgeParams.id) ?? `[ID UNKNOWN]`));
      return false;
    }
    let edge;
    if (isEdge(edgeParams)) {
      edge = edgeParams;
    } else {
      edge = {
        ...edgeParams,
        id: getEdgeId(edgeParams)
      };
    }
    edge = parseEdge(edge, void 0, defaultEdgeOptions);
    if (connectionExists(edge, edges)) {
      return false;
    }
    return edge;
  }
  function updateEdgeAction(edge, newConnection, prevEdge, shouldReplaceId, triggerError) {
    if (!newConnection.source || !newConnection.target) {
      triggerError(new VueFlowError(ErrorCode.EDGE_INVALID, edge.id));
      return false;
    }
    if (!prevEdge) {
      triggerError(new VueFlowError(ErrorCode.EDGE_NOT_FOUND, edge.id));
      return false;
    }
    const { id: id2, ...rest } = edge;
    return {
      ...rest,
      id: shouldReplaceId ? getEdgeId(newConnection) : id2,
      source: newConnection.source,
      target: newConnection.target,
      sourceHandle: newConnection.sourceHandle,
      targetHandle: newConnection.targetHandle
    };
  }
  function createGraphNodes(nodes, findNode, triggerError) {
    const parentNodes = {};
    const nextNodes = [];
    for (let i = 0; i < nodes.length; ++i) {
      const node = nodes[i];
      if (!isNode(node)) {
        triggerError(
          new VueFlowError(ErrorCode.NODE_INVALID, node == null ? void 0 : node.id) || `[ID UNKNOWN|INDEX ${i}]`
        );
        continue;
      }
      const parsed = parseNode(node, findNode(node.id), node.parentNode);
      if (node.parentNode) {
        parentNodes[node.parentNode] = true;
      }
      nextNodes[i] = parsed;
    }
    for (const node of nextNodes) {
      const parentNode = findNode(node.parentNode) || nextNodes.find((n) => n.id === node.parentNode);
      if (node.parentNode && !parentNode) {
        triggerError(new VueFlowError(ErrorCode.NODE_MISSING_PARENT, node.id, node.parentNode));
      }
      if (node.parentNode || parentNodes[node.id]) {
        if (parentNodes[node.id]) {
          node.isParent = true;
        }
        if (parentNode) {
          parentNode.isParent = true;
        }
      }
    }
    return nextNodes;
  }
  function addConnectionToLookup(type, connection, connectionKey, connectionLookup, nodeId, handleId) {
    let key = nodeId;
    const nodeMap = connectionLookup.get(key) || /* @__PURE__ */ new Map();
    connectionLookup.set(key, nodeMap.set(connectionKey, connection));
    key = `${nodeId}-${type}`;
    const typeMap = connectionLookup.get(key) || /* @__PURE__ */ new Map();
    connectionLookup.set(key, typeMap.set(connectionKey, connection));
    if (handleId) {
      key = `${nodeId}-${type}-${handleId}`;
      const handleMap = connectionLookup.get(key) || /* @__PURE__ */ new Map();
      connectionLookup.set(key, handleMap.set(connectionKey, connection));
    }
  }
  function updateConnectionLookup(connectionLookup, edgeLookup, edges) {
    connectionLookup.clear();
    for (const edge of edges) {
      const { source: sourceNode, target: targetNode, sourceHandle = null, targetHandle = null } = edge;
      const connection = { edgeId: edge.id, source: sourceNode, target: targetNode, sourceHandle, targetHandle };
      const sourceKey = `${sourceNode}-${sourceHandle}--${targetNode}-${targetHandle}`;
      const targetKey = `${targetNode}-${targetHandle}--${sourceNode}-${sourceHandle}`;
      addConnectionToLookup("source", connection, targetKey, connectionLookup, sourceNode, sourceHandle);
      addConnectionToLookup("target", connection, sourceKey, connectionLookup, targetNode, targetHandle);
    }
  }
  function areSetsEqual(a, b) {
    if (a.size !== b.size) {
      return false;
    }
    for (const item of a) {
      if (!b.has(item)) {
        return false;
      }
    }
    return true;
  }
  function createGraphEdges(nextEdges, isValidConnection, findNode, findEdge, onError, defaultEdgeOptions, nodes, edges) {
    const validEdges = [];
    for (const edgeOrConnection of nextEdges) {
      const edge = isEdge(edgeOrConnection) ? edgeOrConnection : addEdgeToStore(edgeOrConnection, edges, onError, defaultEdgeOptions);
      if (!edge) {
        continue;
      }
      const sourceNode = findNode(edge.source);
      const targetNode = findNode(edge.target);
      if (!sourceNode || !targetNode) {
        onError(new VueFlowError(ErrorCode.EDGE_SOURCE_TARGET_MISSING, edge.id, edge.source, edge.target));
        continue;
      }
      if (!sourceNode) {
        onError(new VueFlowError(ErrorCode.EDGE_SOURCE_MISSING, edge.id, edge.source));
        continue;
      }
      if (!targetNode) {
        onError(new VueFlowError(ErrorCode.EDGE_TARGET_MISSING, edge.id, edge.target));
        continue;
      }
      if (isValidConnection) {
        const isValid = isValidConnection(edge, {
          edges,
          nodes,
          sourceNode,
          targetNode
        });
        if (!isValid) {
          onError(new VueFlowError(ErrorCode.EDGE_INVALID, edge.id));
          continue;
        }
      }
      const existingEdge = findEdge(edge.id);
      validEdges.push({
        ...parseEdge(edge, existingEdge, defaultEdgeOptions),
        sourceNode,
        targetNode
      });
    }
    return validEdges;
  }
  var VueFlow = /* @__PURE__ */ Symbol("vueFlow");
  var NodeId = /* @__PURE__ */ Symbol("nodeId");
  var NodeRef = /* @__PURE__ */ Symbol("nodeRef");
  var EdgeId = /* @__PURE__ */ Symbol("edgeId");
  var EdgeRef = /* @__PURE__ */ Symbol("edgeRef");
  var Slots = /* @__PURE__ */ Symbol("slots");
  function useDrag(params) {
    const {
      vueFlowRef,
      snapToGrid,
      snapGrid,
      noDragClassName,
      nodeLookup,
      nodeExtent,
      nodeDragThreshold,
      viewport,
      autoPanOnNodeDrag,
      autoPanSpeed,
      nodesDraggable,
      panBy,
      findNode,
      multiSelectionActive,
      nodesSelectionActive,
      selectNodesOnDrag,
      removeSelectedElements,
      addSelectedNodes,
      updateNodePositions,
      emits
    } = useVueFlow();
    const { onStart, onDrag, onStop, onClick, el, disabled, id: id2, selectable, dragHandle } = params;
    const dragging = shallowRef(false);
    let dragItems = [];
    let dragHandler;
    let containerBounds = null;
    let lastPos = { x: void 0, y: void 0 };
    let mousePosition = { x: 0, y: 0 };
    let dragEvent = null;
    let dragStarted = false;
    let nodePositionsChanged = false;
    let autoPanId = 0;
    let autoPanStarted = false;
    const getPointerPosition = useGetPointerPosition();
    const updateNodes = ({ x, y }) => {
      lastPos = { x, y };
      let hasChange = false;
      dragItems = dragItems.map((n) => {
        const nextPosition = { x: x - n.distance.x, y: y - n.distance.y };
        const { computedPosition } = calcNextPosition(
          n,
          snapToGrid.value ? snapPosition(nextPosition, snapGrid.value) : nextPosition,
          emits.error,
          nodeExtent.value,
          n.parentNode ? findNode(n.parentNode) : void 0
        );
        hasChange = hasChange || n.position.x !== computedPosition.x || n.position.y !== computedPosition.y;
        n.position = computedPosition;
        return n;
      });
      nodePositionsChanged = nodePositionsChanged || hasChange;
      if (!hasChange) {
        return;
      }
      updateNodePositions(dragItems, true, true);
      dragging.value = true;
      if (dragEvent) {
        const [currentNode, nodes] = getEventHandlerParams({
          id: id2,
          dragItems,
          findNode
        });
        onDrag({ event: dragEvent, node: currentNode, nodes });
      }
    };
    const autoPan = () => {
      if (!containerBounds) {
        return;
      }
      const [xMovement, yMovement] = calcAutoPan(mousePosition, containerBounds, autoPanSpeed.value);
      if (xMovement !== 0 || yMovement !== 0) {
        const nextPos = {
          x: (lastPos.x ?? 0) - xMovement / viewport.value.zoom,
          y: (lastPos.y ?? 0) - yMovement / viewport.value.zoom
        };
        if (panBy({ x: xMovement, y: yMovement })) {
          updateNodes(nextPos);
        }
      }
      autoPanId = requestAnimationFrame(autoPan);
    };
    const startDrag = (event, nodeEl) => {
      dragStarted = true;
      const node = findNode(id2);
      if (!selectNodesOnDrag.value && !multiSelectionActive.value && node) {
        if (!node.selected) {
          removeSelectedElements();
        }
      }
      if (node && toValue(selectable) && selectNodesOnDrag.value) {
        handleNodeClick(
          node,
          multiSelectionActive.value,
          addSelectedNodes,
          removeSelectedElements,
          nodesSelectionActive,
          false,
          nodeEl
        );
      }
      const pointerPos = getPointerPosition(event.sourceEvent);
      lastPos = pointerPos;
      dragItems = getDragItems(nodeLookup.value, nodesDraggable.value, pointerPos, id2);
      if (dragItems.length) {
        const [currentNode, nodes] = getEventHandlerParams({
          id: id2,
          dragItems,
          findNode
        });
        onStart({ event: event.sourceEvent, node: currentNode, nodes });
      }
    };
    const eventStart = (event, nodeEl) => {
      var _a;
      if (event.sourceEvent.type === "touchmove" && event.sourceEvent.touches.length > 1) {
        return;
      }
      nodePositionsChanged = false;
      if (nodeDragThreshold.value === 0) {
        startDrag(event, nodeEl);
      }
      lastPos = getPointerPosition(event.sourceEvent);
      containerBounds = ((_a = vueFlowRef.value) == null ? void 0 : _a.getBoundingClientRect()) || null;
      mousePosition = getEventPosition(event.sourceEvent, containerBounds);
    };
    const eventDrag = (event, nodeEl) => {
      const pointerPos = getPointerPosition(event.sourceEvent);
      if (!autoPanStarted && dragStarted && autoPanOnNodeDrag.value) {
        autoPanStarted = true;
        autoPan();
      }
      if (!dragStarted) {
        const x = pointerPos.xSnapped - (lastPos.x ?? 0);
        const y = pointerPos.ySnapped - (lastPos.y ?? 0);
        const distance2 = Math.sqrt(x * x + y * y);
        if (distance2 > nodeDragThreshold.value) {
          startDrag(event, nodeEl);
        }
      }
      if ((lastPos.x !== pointerPos.xSnapped || lastPos.y !== pointerPos.ySnapped) && dragItems.length && dragStarted) {
        dragEvent = event.sourceEvent;
        mousePosition = getEventPosition(event.sourceEvent, containerBounds);
        updateNodes(pointerPos);
      }
    };
    const eventEnd = (event) => {
      let isClick = false;
      if (!dragStarted && !dragging.value && !multiSelectionActive.value) {
        const evt = event.sourceEvent;
        const pointerPos = getPointerPosition(evt);
        const x = pointerPos.xSnapped - (lastPos.x ?? 0);
        const y = pointerPos.ySnapped - (lastPos.y ?? 0);
        const distance2 = Math.sqrt(x * x + y * y);
        if (distance2 !== 0 && distance2 <= nodeDragThreshold.value) {
          onClick == null ? void 0 : onClick(evt);
          isClick = true;
        }
      }
      if (dragItems.length && !isClick) {
        if (nodePositionsChanged) {
          updateNodePositions(dragItems, false, false);
          nodePositionsChanged = false;
        }
        const [currentNode, nodes] = getEventHandlerParams({
          id: id2,
          dragItems,
          findNode
        });
        onStop({ event: event.sourceEvent, node: currentNode, nodes });
      }
      dragItems = [];
      dragging.value = false;
      autoPanStarted = false;
      dragStarted = false;
      lastPos = { x: void 0, y: void 0 };
      cancelAnimationFrame(autoPanId);
    };
    watch2([() => toValue(disabled), el], ([isDisabled, nodeEl], _, onCleanup) => {
      if (nodeEl) {
        const selection2 = select(nodeEl);
        if (!isDisabled) {
          dragHandler = drag().on("start", (event) => eventStart(event, nodeEl)).on("drag", (event) => eventDrag(event, nodeEl)).on("end", (event) => eventEnd(event)).filter((event) => {
            const target = event.target;
            const unrefDragHandle = toValue(dragHandle);
            return !event.button && (!noDragClassName.value || !hasSelector(target, `.${noDragClassName.value}`, nodeEl) && (!unrefDragHandle || hasSelector(target, unrefDragHandle, nodeEl)));
          });
          selection2.call(dragHandler);
        }
        onCleanup(() => {
          selection2.on(".drag", null);
          if (dragHandler) {
            dragHandler.on("start", null);
            dragHandler.on("drag", null);
            dragHandler.on("end", null);
          }
        });
      }
    });
    return dragging;
  }
  function createEdgeHooks() {
    return {
      doubleClick: createExtendedEventHook(),
      click: createExtendedEventHook(),
      mouseEnter: createExtendedEventHook(),
      mouseMove: createExtendedEventHook(),
      mouseLeave: createExtendedEventHook(),
      contextMenu: createExtendedEventHook(),
      updateStart: createExtendedEventHook(),
      update: createExtendedEventHook(),
      updateEnd: createExtendedEventHook()
    };
  }
  function useEdgeHooks(edge, emits) {
    const edgeHooks = createEdgeHooks();
    edgeHooks.doubleClick.on((event) => {
      var _a, _b;
      emits.edgeDoubleClick(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.doubleClick) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.click.on((event) => {
      var _a, _b;
      emits.edgeClick(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.click) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.mouseEnter.on((event) => {
      var _a, _b;
      emits.edgeMouseEnter(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.mouseEnter) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.mouseMove.on((event) => {
      var _a, _b;
      emits.edgeMouseMove(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.mouseMove) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.mouseLeave.on((event) => {
      var _a, _b;
      emits.edgeMouseLeave(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.mouseLeave) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.contextMenu.on((event) => {
      var _a, _b;
      emits.edgeContextMenu(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.contextMenu) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.updateStart.on((event) => {
      var _a, _b;
      emits.edgeUpdateStart(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.updateStart) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.update.on((event) => {
      var _a, _b;
      emits.edgeUpdate(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.update) == null ? void 0 : _b.call(_a, event);
    });
    edgeHooks.updateEnd.on((event) => {
      var _a, _b;
      emits.edgeUpdateEnd(event);
      (_b = (_a = edge.events) == null ? void 0 : _a.updateEnd) == null ? void 0 : _b.call(_a, event);
    });
    return Object.entries(edgeHooks).reduce(
      (hooks, [key, value]) => {
        hooks.emit[key] = value.trigger;
        hooks.on[key] = value.on;
        return hooks;
      },
      { emit: {}, on: {} }
    );
  }
  function useGetPointerPosition() {
    const { viewport, snapGrid, snapToGrid, vueFlowRef } = useVueFlow();
    return (event) => {
      var _a;
      const containerBounds = ((_a = vueFlowRef.value) == null ? void 0 : _a.getBoundingClientRect()) ?? { left: 0, top: 0 };
      const evt = isUseDragEvent(event) ? event.sourceEvent : event;
      const { x, y } = getEventPosition(evt, containerBounds);
      const pointerPos = pointToRendererPoint({ x, y }, viewport.value);
      const { x: xSnapped, y: ySnapped } = snapToGrid.value ? snapPosition(pointerPos, snapGrid.value) : pointerPos;
      return {
        xSnapped,
        ySnapped,
        ...pointerPos
      };
    };
  }
  function alwaysValid() {
    return true;
  }
  function useHandle({
    handleId,
    nodeId,
    type,
    isValidConnection,
    edgeUpdaterType,
    onEdgeUpdate,
    onEdgeUpdateEnd
  }) {
    const {
      id: flowId,
      vueFlowRef,
      connectionMode,
      connectionRadius,
      connectOnClick,
      connectionClickStartHandle,
      nodesConnectable,
      autoPanOnConnect,
      autoPanSpeed,
      findNode,
      panBy,
      startConnection,
      updateConnection,
      endConnection,
      emits,
      viewport,
      edges,
      nodes,
      isValidConnection: isValidConnectionProp,
      nodeLookup
    } = useVueFlow();
    let connection = null;
    let isValid = false;
    let handleDomNode = null;
    function handlePointerDown(event) {
      var _a;
      const isTarget = toValue(type) === "target";
      const isMouseTriggered = isMouseEvent(event);
      const doc2 = getHostForElement(event.target);
      const clickedHandle = event.currentTarget;
      if (clickedHandle && (isMouseTriggered && event.button === 0 || !isMouseTriggered)) {
        let onPointerMove = function(event2) {
          connectionPosition = getEventPosition(event2, containerBounds);
          closestHandle = getClosestHandle(
            pointToRendererPoint(connectionPosition, viewport.value, false, [1, 1]),
            connectionRadius.value,
            nodeLookup.value,
            fromHandle
          );
          if (!autoPanStarted) {
            autoPan();
            autoPanStarted = true;
          }
          const result = isValidHandle(
            event2,
            {
              handle: closestHandle,
              connectionMode: connectionMode.value,
              fromNodeId: toValue(nodeId),
              fromHandleId: toValue(handleId),
              fromType: isTarget ? "target" : "source",
              isValidConnection: isValidConnectionHandler,
              doc: doc2,
              lib: "vue",
              flowId,
              nodeLookup: nodeLookup.value
            },
            edges.value,
            nodes.value,
            findNode,
            nodeLookup.value
          );
          handleDomNode = result.handleDomNode;
          connection = result.connection;
          isValid = isConnectionValid(!!closestHandle, result.isValid);
          const newConnection2 = {
            // from stays the same
            ...previousConnection,
            isValid,
            to: result.toHandle && isValid ? rendererPointToPoint({ x: result.toHandle.x, y: result.toHandle.y }, viewport.value) : connectionPosition,
            toHandle: result.toHandle,
            toPosition: isValid && result.toHandle ? result.toHandle.position : oppositePosition[fromHandle.position],
            toNode: result.toHandle ? nodeLookup.value.get(result.toHandle.nodeId) : null
          };
          if (isValid && closestHandle && (previousConnection == null ? void 0 : previousConnection.toHandle) && newConnection2.toHandle && previousConnection.toHandle.type === newConnection2.toHandle.type && previousConnection.toHandle.nodeId === newConnection2.toHandle.nodeId && previousConnection.toHandle.id === newConnection2.toHandle.id && previousConnection.to.x === newConnection2.to.x && previousConnection.to.y === newConnection2.to.y) {
            return;
          }
          const connectingHandle = closestHandle ?? result.toHandle;
          updateConnection(
            connectingHandle && isValid ? rendererPointToPoint(
              {
                x: connectingHandle.x,
                y: connectingHandle.y
              },
              viewport.value
            ) : connectionPosition,
            connectingHandle,
            getConnectionStatus(!!connectingHandle, isValid)
          );
          previousConnection = newConnection2;
          if (!closestHandle && !isValid && !handleDomNode) {
            return resetRecentHandle(prevActiveHandle);
          }
          if (connection && connection.source !== connection.target && handleDomNode) {
            resetRecentHandle(prevActiveHandle);
            prevActiveHandle = handleDomNode;
            handleDomNode.classList.add("connecting", "vue-flow__handle-connecting");
            handleDomNode.classList.toggle("valid", !!isValid);
            handleDomNode.classList.toggle("vue-flow__handle-valid", !!isValid);
          }
        }, onPointerUp = function(event2) {
          if ("touches" in event2 && event2.touches.length > 0) {
            return;
          }
          if ((closestHandle || handleDomNode) && connection && isValid) {
            if (!onEdgeUpdate) {
              emits.connect(connection);
            } else {
              onEdgeUpdate(event2, connection);
            }
          }
          emits.connectEnd(event2);
          if (edgeUpdaterType) {
            onEdgeUpdateEnd == null ? void 0 : onEdgeUpdateEnd(event2);
          }
          resetRecentHandle(prevActiveHandle);
          cancelAnimationFrame(autoPanId);
          endConnection(event2);
          autoPanStarted = false;
          isValid = false;
          connection = null;
          handleDomNode = null;
          doc2.removeEventListener("mousemove", onPointerMove);
          doc2.removeEventListener("mouseup", onPointerUp);
          doc2.removeEventListener("touchmove", onPointerMove);
          doc2.removeEventListener("touchend", onPointerUp);
        };
        const node = findNode(toValue(nodeId));
        let isValidConnectionHandler = toValue(isValidConnection) || isValidConnectionProp.value || alwaysValid;
        if (!isValidConnectionHandler && node) {
          isValidConnectionHandler = (!isTarget ? node.isValidTargetPos : node.isValidSourcePos) || alwaysValid;
        }
        let closestHandle;
        let autoPanId = 0;
        const { x, y } = getEventPosition(event);
        const handleType = getHandleType(toValue(edgeUpdaterType), clickedHandle);
        const containerBounds = (_a = vueFlowRef.value) == null ? void 0 : _a.getBoundingClientRect();
        if (!containerBounds || !handleType) {
          return;
        }
        const fromHandleInternal = getHandle(toValue(nodeId), handleType, toValue(handleId), nodeLookup.value, connectionMode.value);
        if (!fromHandleInternal) {
          return;
        }
        let prevActiveHandle;
        let connectionPosition = getEventPosition(event, containerBounds);
        let autoPanStarted = false;
        const autoPan = () => {
          if (!autoPanOnConnect.value) {
            return;
          }
          const [xMovement, yMovement] = calcAutoPan(connectionPosition, containerBounds, autoPanSpeed.value);
          panBy({ x: xMovement, y: yMovement });
          autoPanId = requestAnimationFrame(autoPan);
        };
        const fromHandle = {
          ...fromHandleInternal,
          nodeId: toValue(nodeId),
          type: handleType,
          position: fromHandleInternal.position
        };
        const fromNodeInternal = nodeLookup.value.get(toValue(nodeId));
        const from = getHandlePosition(fromNodeInternal, fromHandle, Position.Left, true);
        const newConnection = {
          inProgress: true,
          isValid: null,
          from,
          fromHandle,
          fromPosition: fromHandle.position,
          fromNode: fromNodeInternal,
          to: connectionPosition,
          toHandle: null,
          toPosition: oppositePosition[fromHandle.position],
          toNode: null
        };
        startConnection(
          {
            nodeId: toValue(nodeId),
            id: toValue(handleId),
            type: handleType,
            position: (clickedHandle == null ? void 0 : clickedHandle.getAttribute("data-handlepos")) || Position.Top,
            ...connectionPosition
          },
          {
            x: x - containerBounds.left,
            y: y - containerBounds.top
          }
        );
        emits.connectStart({ event, nodeId: toValue(nodeId), handleId: toValue(handleId), handleType });
        let previousConnection = newConnection;
        doc2.addEventListener("mousemove", onPointerMove);
        doc2.addEventListener("mouseup", onPointerUp);
        doc2.addEventListener("touchmove", onPointerMove);
        doc2.addEventListener("touchend", onPointerUp);
      }
    }
    function handleClick(event) {
      var _a, _b;
      if (!connectOnClick.value) {
        return;
      }
      const isTarget = toValue(type) === "target";
      if (!connectionClickStartHandle.value) {
        emits.clickConnectStart({ event, nodeId: toValue(nodeId), handleId: toValue(handleId) });
        startConnection(
          {
            nodeId: toValue(nodeId),
            type: toValue(type),
            id: toValue(handleId),
            position: Position.Top,
            ...getEventPosition(event)
          },
          void 0,
          true
        );
        return;
      }
      let isValidConnectionHandler = toValue(isValidConnection) || isValidConnectionProp.value || alwaysValid;
      const node = findNode(toValue(nodeId));
      if (!isValidConnectionHandler && node) {
        isValidConnectionHandler = (!isTarget ? node.isValidTargetPos : node.isValidSourcePos) || alwaysValid;
      }
      if (node && (typeof node.connectable === "undefined" ? nodesConnectable.value : node.connectable) === false) {
        return;
      }
      const doc2 = getHostForElement(event.target);
      const result = isValidHandle(
        event,
        {
          handle: {
            nodeId: toValue(nodeId),
            id: toValue(handleId),
            type: toValue(type),
            position: Position.Top,
            ...getEventPosition(event)
          },
          connectionMode: connectionMode.value,
          fromNodeId: connectionClickStartHandle.value.nodeId,
          fromHandleId: connectionClickStartHandle.value.id ?? null,
          fromType: connectionClickStartHandle.value.type,
          isValidConnection: isValidConnectionHandler,
          doc: doc2,
          lib: "vue",
          flowId,
          nodeLookup: nodeLookup.value
        },
        edges.value,
        nodes.value,
        findNode,
        nodeLookup.value
      );
      const isOwnHandle = ((_a = result.connection) == null ? void 0 : _a.source) === ((_b = result.connection) == null ? void 0 : _b.target);
      if (result.isValid && result.connection && !isOwnHandle) {
        emits.connect(result.connection);
      }
      emits.clickConnectEnd(event);
      endConnection(event, true);
    }
    return {
      handlePointerDown,
      handleClick
    };
  }
  function useNodeId() {
    return inject(NodeId, "");
  }
  function useNode(id2) {
    const nodeId = id2 ?? useNodeId() ?? "";
    const nodeEl = inject(NodeRef, ref(null));
    const { findNode, edges, emits } = useVueFlow();
    const node = findNode(nodeId);
    if (!node) {
      emits.error(new VueFlowError(ErrorCode.NODE_NOT_FOUND, nodeId));
    }
    return {
      id: nodeId,
      nodeEl,
      node,
      parentNode: computed2(() => findNode(node.parentNode)),
      connectedEdges: computed2(() => getConnectedEdges([node], edges.value))
    };
  }
  function createNodeHooks() {
    return {
      doubleClick: createExtendedEventHook(),
      click: createExtendedEventHook(),
      mouseEnter: createExtendedEventHook(),
      mouseMove: createExtendedEventHook(),
      mouseLeave: createExtendedEventHook(),
      contextMenu: createExtendedEventHook(),
      dragStart: createExtendedEventHook(),
      drag: createExtendedEventHook(),
      dragStop: createExtendedEventHook()
    };
  }
  function useNodeHooks(node, emits) {
    const nodeHooks = createNodeHooks();
    nodeHooks.doubleClick.on((event) => {
      var _a, _b;
      emits.nodeDoubleClick(event);
      (_b = (_a = node.events) == null ? void 0 : _a.doubleClick) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.click.on((event) => {
      var _a, _b;
      emits.nodeClick(event);
      (_b = (_a = node.events) == null ? void 0 : _a.click) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.mouseEnter.on((event) => {
      var _a, _b;
      emits.nodeMouseEnter(event);
      (_b = (_a = node.events) == null ? void 0 : _a.mouseEnter) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.mouseMove.on((event) => {
      var _a, _b;
      emits.nodeMouseMove(event);
      (_b = (_a = node.events) == null ? void 0 : _a.mouseMove) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.mouseLeave.on((event) => {
      var _a, _b;
      emits.nodeMouseLeave(event);
      (_b = (_a = node.events) == null ? void 0 : _a.mouseLeave) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.contextMenu.on((event) => {
      var _a, _b;
      emits.nodeContextMenu(event);
      (_b = (_a = node.events) == null ? void 0 : _a.contextMenu) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.dragStart.on((event) => {
      var _a, _b;
      emits.nodeDragStart(event);
      (_b = (_a = node.events) == null ? void 0 : _a.dragStart) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.drag.on((event) => {
      var _a, _b;
      emits.nodeDrag(event);
      (_b = (_a = node.events) == null ? void 0 : _a.drag) == null ? void 0 : _b.call(_a, event);
    });
    nodeHooks.dragStop.on((event) => {
      var _a, _b;
      emits.nodeDragStop(event);
      (_b = (_a = node.events) == null ? void 0 : _a.dragStop) == null ? void 0 : _b.call(_a, event);
    });
    return Object.entries(nodeHooks).reduce(
      (hooks, [key, value]) => {
        hooks.emit[key] = value.trigger;
        hooks.on[key] = value.on;
        return hooks;
      },
      { emit: {}, on: {} }
    );
  }
  function useUpdateNodePositions() {
    const { getSelectedNodes, nodeExtent, updateNodePositions, findNode, snapGrid, snapToGrid, nodesDraggable, emits } = useVueFlow();
    return (positionDiff, isShiftPressed = false) => {
      const xVelo = snapToGrid.value ? snapGrid.value[0] : 5;
      const yVelo = snapToGrid.value ? snapGrid.value[1] : 5;
      const factor = isShiftPressed ? 4 : 1;
      const positionDiffX = positionDiff.x * xVelo * factor;
      const positionDiffY = positionDiff.y * yVelo * factor;
      const nodeUpdates = [];
      for (const node of getSelectedNodes.value) {
        if (node.draggable || nodesDraggable && typeof node.draggable === "undefined") {
          const nextPosition = { x: node.computedPosition.x + positionDiffX, y: node.computedPosition.y + positionDiffY };
          const { position } = calcNextPosition(
            node,
            nextPosition,
            emits.error,
            nodeExtent.value,
            node.parentNode ? findNode(node.parentNode) : void 0
          );
          nodeUpdates.push({
            id: node.id,
            position,
            from: node.position,
            distance: { x: positionDiff.x, y: positionDiff.y },
            dimensions: node.dimensions
          });
        }
      }
      updateNodePositions(nodeUpdates, true, false);
    };
  }
  var DEFAULT_PADDING = 0.1;
  var defaultEase = (t) => ((t *= 2) <= 1 ? t * t * t : (t -= 2) * t * t + 2) / 2;
  function noop() {
    warn3("Viewport not initialized yet.");
    return Promise.resolve(false);
  }
  var initialViewportHelper = {
    zoomIn: noop,
    zoomOut: noop,
    zoomTo: noop,
    fitView: noop,
    setCenter: noop,
    fitBounds: noop,
    project: (position) => position,
    screenToFlowCoordinate: (position) => position,
    flowToScreenCoordinate: (position) => position,
    setViewport: noop,
    setTransform: noop,
    getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
    getTransform: () => ({ x: 0, y: 0, zoom: 1 }),
    viewportInitialized: false
  };
  function useViewportHelper(state) {
    function zoom2(scale, transitionOptions) {
      return new Promise((resolve2) => {
        if (state.d3Selection && state.d3Zoom) {
          state.d3Zoom.interpolate((transitionOptions == null ? void 0 : transitionOptions.interpolate) === "linear" ? interpolate$1 : interpolateZoom).scaleBy(
            getD3Transition(state.d3Selection, transitionOptions == null ? void 0 : transitionOptions.duration, transitionOptions == null ? void 0 : transitionOptions.ease, () => {
              resolve2(true);
            }),
            scale
          );
        } else {
          resolve2(false);
        }
      });
    }
    function transformViewport(x, y, zoom22, transitionOptions) {
      return new Promise((resolve2) => {
        var _a;
        const { x: clampedX, y: clampedY } = clampPosition({ x: -x, y: -y }, state.translateExtent);
        const nextTransform = identity.translate(-clampedX, -clampedY).scale(zoom22);
        if (state.d3Selection && state.d3Zoom) {
          (_a = state.d3Zoom) == null ? void 0 : _a.interpolate((transitionOptions == null ? void 0 : transitionOptions.interpolate) === "linear" ? interpolate$1 : interpolateZoom).transform(
            getD3Transition(state.d3Selection, transitionOptions == null ? void 0 : transitionOptions.duration, transitionOptions == null ? void 0 : transitionOptions.ease, () => {
              resolve2(true);
            }),
            nextTransform
          );
        } else {
          resolve2(false);
        }
      });
    }
    return computed2(() => {
      const isInitialized = state.d3Zoom && state.d3Selection && state.dimensions.width && state.dimensions.height;
      if (!isInitialized) {
        return initialViewportHelper;
      }
      return {
        viewportInitialized: true,
        // todo: allow passing scale as option
        zoomIn: (options) => {
          return zoom2(1.2, options);
        },
        zoomOut: (options) => {
          return zoom2(1 / 1.2, options);
        },
        zoomTo: (zoomLevel, options) => {
          return new Promise((resolve2) => {
            if (state.d3Selection && state.d3Zoom) {
              state.d3Zoom.interpolate((options == null ? void 0 : options.interpolate) === "linear" ? interpolate$1 : interpolateZoom).scaleTo(
                getD3Transition(state.d3Selection, options == null ? void 0 : options.duration, options == null ? void 0 : options.ease, () => {
                  resolve2(true);
                }),
                zoomLevel
              );
            } else {
              resolve2(false);
            }
          });
        },
        setViewport: (transform, options) => {
          return transformViewport(transform.x, transform.y, transform.zoom, options);
        },
        setTransform: (transform, options) => {
          return transformViewport(transform.x, transform.y, transform.zoom, options);
        },
        getViewport: () => ({
          x: state.viewport.x,
          y: state.viewport.y,
          zoom: state.viewport.zoom
        }),
        getTransform: () => {
          return {
            x: state.viewport.x,
            y: state.viewport.y,
            zoom: state.viewport.zoom
          };
        },
        fitView: (options = {
          padding: DEFAULT_PADDING,
          includeHiddenNodes: false,
          duration: 0
        }) => {
          var _a, _b;
          const nodesToFit = [];
          for (const node of state.nodes) {
            const isVisible = node.dimensions.width && node.dimensions.height && ((options == null ? void 0 : options.includeHiddenNodes) || !node.hidden);
            if (isVisible) {
              if (!((_a = options.nodes) == null ? void 0 : _a.length) || ((_b = options.nodes) == null ? void 0 : _b.length) && options.nodes.includes(node.id)) {
                nodesToFit.push(node);
              }
            }
          }
          if (!nodesToFit.length) {
            return Promise.resolve(false);
          }
          const bounds = getRectOfNodes(nodesToFit);
          const { x, y, zoom: zoom22 } = getTransformForBounds(
            bounds,
            state.dimensions.width,
            state.dimensions.height,
            options.minZoom ?? state.minZoom,
            options.maxZoom ?? state.maxZoom,
            options.padding ?? DEFAULT_PADDING
          );
          return transformViewport(x, y, zoom22, options);
        },
        setCenter: (x, y, options) => {
          const nextZoom = typeof (options == null ? void 0 : options.zoom) !== "undefined" ? options.zoom : state.maxZoom;
          const centerX = state.dimensions.width / 2 - x * nextZoom;
          const centerY = state.dimensions.height / 2 - y * nextZoom;
          return transformViewport(centerX, centerY, nextZoom, options);
        },
        fitBounds: (bounds, options = { padding: DEFAULT_PADDING }) => {
          const { x, y, zoom: zoom22 } = getTransformForBounds(
            bounds,
            state.dimensions.width,
            state.dimensions.height,
            state.minZoom,
            state.maxZoom,
            options.padding ?? DEFAULT_PADDING
          );
          return transformViewport(x, y, zoom22, options);
        },
        project: (position) => pointToRendererPoint(position, state.viewport, state.snapToGrid, state.snapGrid),
        screenToFlowCoordinate: (position) => {
          if (state.vueFlowRef) {
            const { x: domX, y: domY } = state.vueFlowRef.getBoundingClientRect();
            const correctedPosition = {
              x: position.x - domX,
              y: position.y - domY
            };
            return pointToRendererPoint(correctedPosition, state.viewport, state.snapToGrid, state.snapGrid);
          }
          return { x: 0, y: 0 };
        },
        flowToScreenCoordinate: (position) => {
          if (state.vueFlowRef) {
            const { x: domX, y: domY } = state.vueFlowRef.getBoundingClientRect();
            const correctedPosition = {
              x: position.x + domX,
              y: position.y + domY
            };
            return rendererPointToPoint(correctedPosition, state.viewport);
          }
          return { x: 0, y: 0 };
        }
      };
    });
  }
  function getD3Transition(selection2, duration = 0, ease = defaultEase, onEnd = () => {
  }) {
    const hasDuration = typeof duration === "number" && duration > 0;
    if (!hasDuration) {
      onEnd();
    }
    return hasDuration ? selection2.transition().duration(duration).ease(ease).on("end", onEnd) : selection2;
  }
  function useWatchProps(models, props, store) {
    const scope = effectScope(true);
    scope.run(() => {
      const watchModelValue = () => {
        scope.run(() => {
          let pauseModel;
          let pauseStore;
          let immediateStore = !!(store.nodes.value.length || store.edges.value.length);
          pauseModel = watchPausable([models.modelValue, () => {
            var _a, _b;
            return (_b = (_a = models.modelValue) == null ? void 0 : _a.value) == null ? void 0 : _b.length;
          }], ([elements]) => {
            if (elements && Array.isArray(elements)) {
              pauseStore == null ? void 0 : pauseStore.pause();
              store.setElements(elements);
              if (!pauseStore && !immediateStore && elements.length) {
                immediateStore = true;
              } else {
                pauseStore == null ? void 0 : pauseStore.resume();
              }
            }
          });
          pauseStore = watchPausable(
            [store.nodes, store.edges, () => store.edges.value.length, () => store.nodes.value.length],
            ([nodes, edges]) => {
              var _a;
              if (((_a = models.modelValue) == null ? void 0 : _a.value) && Array.isArray(models.modelValue.value)) {
                pauseModel == null ? void 0 : pauseModel.pause();
                models.modelValue.value = [...nodes, ...edges];
                nextTick(() => {
                  pauseModel == null ? void 0 : pauseModel.resume();
                });
              }
            },
            { immediate: immediateStore }
          );
          onScopeDispose(() => {
            pauseModel == null ? void 0 : pauseModel.stop();
            pauseStore == null ? void 0 : pauseStore.stop();
          });
        });
      };
      const watchNodesValue = () => {
        scope.run(() => {
          let pauseModel;
          let pauseStore;
          let immediateStore = !!store.nodes.value.length;
          pauseModel = watchPausable([models.nodes, () => {
            var _a, _b;
            return (_b = (_a = models.nodes) == null ? void 0 : _a.value) == null ? void 0 : _b.length;
          }], ([nodes]) => {
            if (nodes && Array.isArray(nodes)) {
              pauseStore == null ? void 0 : pauseStore.pause();
              store.setNodes(nodes);
              if (!pauseStore && !immediateStore && nodes.length) {
                immediateStore = true;
              } else {
                pauseStore == null ? void 0 : pauseStore.resume();
              }
            }
          });
          pauseStore = watchPausable(
            [store.nodes, () => store.nodes.value.length],
            ([nodes]) => {
              var _a;
              if (((_a = models.nodes) == null ? void 0 : _a.value) && Array.isArray(models.nodes.value)) {
                pauseModel == null ? void 0 : pauseModel.pause();
                models.nodes.value = [...nodes];
                nextTick(() => {
                  pauseModel == null ? void 0 : pauseModel.resume();
                });
              }
            },
            { immediate: immediateStore }
          );
          onScopeDispose(() => {
            pauseModel == null ? void 0 : pauseModel.stop();
            pauseStore == null ? void 0 : pauseStore.stop();
          });
        });
      };
      const watchEdgesValue = () => {
        scope.run(() => {
          let pauseModel;
          let pauseStore;
          let immediateStore = !!store.edges.value.length;
          pauseModel = watchPausable([models.edges, () => {
            var _a, _b;
            return (_b = (_a = models.edges) == null ? void 0 : _a.value) == null ? void 0 : _b.length;
          }], ([edges]) => {
            if (edges && Array.isArray(edges)) {
              pauseStore == null ? void 0 : pauseStore.pause();
              store.setEdges(edges);
              if (!pauseStore && !immediateStore && edges.length) {
                immediateStore = true;
              } else {
                pauseStore == null ? void 0 : pauseStore.resume();
              }
            }
          });
          pauseStore = watchPausable(
            [store.edges, () => store.edges.value.length],
            ([edges]) => {
              var _a;
              if (((_a = models.edges) == null ? void 0 : _a.value) && Array.isArray(models.edges.value)) {
                pauseModel == null ? void 0 : pauseModel.pause();
                models.edges.value = [...edges];
                nextTick(() => {
                  pauseModel == null ? void 0 : pauseModel.resume();
                });
              }
            },
            { immediate: immediateStore }
          );
          onScopeDispose(() => {
            pauseModel == null ? void 0 : pauseModel.stop();
            pauseStore == null ? void 0 : pauseStore.stop();
          });
        });
      };
      const watchMaxZoom = () => {
        scope.run(() => {
          watch2(
            () => props.maxZoom,
            () => {
              if (props.maxZoom && isDef(props.maxZoom)) {
                store.setMaxZoom(props.maxZoom);
              }
            },
            {
              immediate: true
            }
          );
        });
      };
      const watchMinZoom = () => {
        scope.run(() => {
          watch2(
            () => props.minZoom,
            () => {
              if (props.minZoom && isDef(props.minZoom)) {
                store.setMinZoom(props.minZoom);
              }
            },
            { immediate: true }
          );
        });
      };
      const watchTranslateExtent = () => {
        scope.run(() => {
          watch2(
            () => props.translateExtent,
            () => {
              if (props.translateExtent && isDef(props.translateExtent)) {
                store.setTranslateExtent(props.translateExtent);
              }
            },
            {
              immediate: true
            }
          );
        });
      };
      const watchNodeExtent = () => {
        scope.run(() => {
          watch2(
            () => props.nodeExtent,
            () => {
              if (props.nodeExtent && isDef(props.nodeExtent)) {
                store.setNodeExtent(props.nodeExtent);
              }
            },
            {
              immediate: true
            }
          );
        });
      };
      const watchApplyDefault = () => {
        scope.run(() => {
          watch2(
            () => props.applyDefault,
            () => {
              if (isDef(props.applyDefault)) {
                store.applyDefault.value = props.applyDefault;
              }
            },
            {
              immediate: true
            }
          );
        });
      };
      const watchAutoConnect = () => {
        scope.run(() => {
          const autoConnector = async (params) => {
            let connection = params;
            if (typeof props.autoConnect === "function") {
              connection = await props.autoConnect(params);
            }
            if (connection !== false) {
              store.addEdges([connection]);
            }
          };
          watch2(
            () => props.autoConnect,
            () => {
              if (isDef(props.autoConnect)) {
                store.autoConnect.value = props.autoConnect;
              }
            },
            { immediate: true }
          );
          watch2(
            store.autoConnect,
            (autoConnectEnabled, _, onCleanup) => {
              if (autoConnectEnabled) {
                store.onConnect(autoConnector);
              } else {
                store.hooks.value.connect.off(autoConnector);
              }
              onCleanup(() => {
                store.hooks.value.connect.off(autoConnector);
              });
            },
            { immediate: true }
          );
        });
      };
      const watchRest = () => {
        const skip = [
          "id",
          "modelValue",
          "translateExtent",
          "nodeExtent",
          "edges",
          "nodes",
          "maxZoom",
          "minZoom",
          "applyDefault",
          "autoConnect"
        ];
        for (const key of Object.keys(props)) {
          const propKey = key;
          if (!skip.includes(propKey)) {
            const propValue = toRef(() => props[propKey]);
            const storeRef = store[propKey];
            if (isRef2(storeRef)) {
              scope.run(() => {
                watch2(
                  propValue,
                  (nextValue) => {
                    if (isDef(nextValue)) {
                      storeRef.value = nextValue;
                    }
                  },
                  { immediate: true }
                );
              });
            }
          }
        }
      };
      const runAll = () => {
        watchModelValue();
        watchNodesValue();
        watchEdgesValue();
        watchMinZoom();
        watchMaxZoom();
        watchTranslateExtent();
        watchNodeExtent();
        watchApplyDefault();
        watchAutoConnect();
        watchRest();
      };
      runAll();
    });
    return () => scope.stop();
  }
  function createHooks() {
    return {
      edgesChange: createExtendedEventHook(),
      nodesChange: createExtendedEventHook(),
      nodeDoubleClick: createExtendedEventHook(),
      nodeClick: createExtendedEventHook(),
      nodeMouseEnter: createExtendedEventHook(),
      nodeMouseMove: createExtendedEventHook(),
      nodeMouseLeave: createExtendedEventHook(),
      nodeContextMenu: createExtendedEventHook(),
      nodeDragStart: createExtendedEventHook(),
      nodeDrag: createExtendedEventHook(),
      nodeDragStop: createExtendedEventHook(),
      nodesInitialized: createExtendedEventHook(),
      miniMapNodeClick: createExtendedEventHook(),
      miniMapNodeDoubleClick: createExtendedEventHook(),
      miniMapNodeMouseEnter: createExtendedEventHook(),
      miniMapNodeMouseMove: createExtendedEventHook(),
      miniMapNodeMouseLeave: createExtendedEventHook(),
      connect: createExtendedEventHook(),
      connectStart: createExtendedEventHook(),
      connectEnd: createExtendedEventHook(),
      clickConnectStart: createExtendedEventHook(),
      clickConnectEnd: createExtendedEventHook(),
      paneReady: createExtendedEventHook(),
      init: createExtendedEventHook(),
      move: createExtendedEventHook(),
      moveStart: createExtendedEventHook(),
      moveEnd: createExtendedEventHook(),
      selectionDragStart: createExtendedEventHook(),
      selectionDrag: createExtendedEventHook(),
      selectionDragStop: createExtendedEventHook(),
      selectionContextMenu: createExtendedEventHook(),
      selectionStart: createExtendedEventHook(),
      selectionEnd: createExtendedEventHook(),
      viewportChangeStart: createExtendedEventHook(),
      viewportChange: createExtendedEventHook(),
      viewportChangeEnd: createExtendedEventHook(),
      paneScroll: createExtendedEventHook(),
      paneClick: createExtendedEventHook(),
      paneContextMenu: createExtendedEventHook(),
      paneMouseEnter: createExtendedEventHook(),
      paneMouseMove: createExtendedEventHook(),
      paneMouseLeave: createExtendedEventHook(),
      edgeContextMenu: createExtendedEventHook(),
      edgeMouseEnter: createExtendedEventHook(),
      edgeMouseMove: createExtendedEventHook(),
      edgeMouseLeave: createExtendedEventHook(),
      edgeDoubleClick: createExtendedEventHook(),
      edgeClick: createExtendedEventHook(),
      edgeUpdateStart: createExtendedEventHook(),
      edgeUpdate: createExtendedEventHook(),
      edgeUpdateEnd: createExtendedEventHook(),
      updateNodeInternals: createExtendedEventHook(),
      error: createExtendedEventHook((err) => warn3(err.message))
    };
  }
  function useHooks(emit2, hooks) {
    const inst = getCurrentInstance();
    onBeforeMount(() => {
      for (const [key, value] of Object.entries(hooks.value)) {
        const listener = (data) => {
          emit2(key, data);
        };
        value.setEmitter(listener);
        tryOnScopeDispose(value.removeEmitter);
        value.setHasEmitListeners(() => hasVNodeListener(key));
        tryOnScopeDispose(value.removeHasEmitListeners);
      }
    });
    function hasVNodeListener(event) {
      var _a;
      const key = toHandlerKey2(event);
      const h2 = (_a = inst == null ? void 0 : inst.vnode.props) == null ? void 0 : _a[key];
      return !!h2;
    }
  }
  function toHandlerKey2(event) {
    const [head, ...rest] = event.split(":");
    const camel = head.replace(/(?:^|-)(\w)/g, (_, c) => c.toUpperCase());
    return `on${camel}${rest.length ? `:${rest.join(":")}` : ""}`;
  }
  function useState() {
    return {
      vueFlowRef: null,
      viewportRef: null,
      nodes: [],
      edges: [],
      connectionLookup: /* @__PURE__ */ new Map(),
      nodeTypes: {},
      edgeTypes: {},
      initialized: false,
      dimensions: {
        width: 0,
        height: 0
      },
      viewport: { x: 0, y: 0, zoom: 1 },
      d3Zoom: null,
      d3Selection: null,
      d3ZoomHandler: null,
      minZoom: 0.5,
      maxZoom: 2,
      translateExtent: [
        [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
        [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY]
      ],
      nodeExtent: [
        [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
        [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY]
      ],
      selectionMode: SelectionMode.Full,
      paneDragging: false,
      preventScrolling: true,
      zoomOnScroll: true,
      zoomOnPinch: true,
      zoomOnDoubleClick: true,
      panOnScroll: false,
      panOnScrollSpeed: 0.5,
      panOnScrollMode: PanOnScrollMode.Free,
      paneClickDistance: 0,
      panOnDrag: true,
      edgeUpdaterRadius: 10,
      onlyRenderVisibleElements: false,
      defaultViewport: { x: 0, y: 0, zoom: 1 },
      nodesSelectionActive: false,
      userSelectionActive: false,
      userSelectionRect: null,
      defaultMarkerColor: "#b1b1b7",
      connectionLineStyle: {},
      connectionLineType: null,
      connectionLineOptions: {
        type: ConnectionLineType.Bezier,
        style: {}
      },
      connectionMode: ConnectionMode.Loose,
      connectionStartHandle: null,
      connectionEndHandle: null,
      connectionClickStartHandle: null,
      connectionPosition: { x: Number.NaN, y: Number.NaN },
      connectionRadius: 20,
      connectOnClick: true,
      connectionStatus: null,
      isValidConnection: null,
      snapGrid: [15, 15],
      snapToGrid: false,
      edgesUpdatable: false,
      edgesFocusable: true,
      nodesFocusable: true,
      nodesConnectable: true,
      nodesDraggable: true,
      nodeDragThreshold: 1,
      elementsSelectable: true,
      selectNodesOnDrag: true,
      multiSelectionActive: false,
      selectionKeyCode: "Shift",
      multiSelectionKeyCode: isMacOs() ? "Meta" : "Control",
      zoomActivationKeyCode: isMacOs() ? "Meta" : "Control",
      deleteKeyCode: "Backspace",
      panActivationKeyCode: "Space",
      hooks: createHooks(),
      applyDefault: true,
      autoConnect: false,
      fitViewOnInit: false,
      fitViewOnInitDone: false,
      noDragClassName: "nodrag",
      noWheelClassName: "nowheel",
      noPanClassName: "nopan",
      defaultEdgeOptions: void 0,
      elevateEdgesOnSelect: false,
      elevateNodesOnSelect: true,
      autoPanOnNodeDrag: true,
      autoPanOnConnect: true,
      autoPanSpeed: 15,
      disableKeyboardA11y: false,
      ariaLiveMessage: ""
    };
  }
  var storeOptionsToSkip = [
    "id",
    "vueFlowRef",
    "viewportRef",
    "initialized",
    "modelValue",
    "nodes",
    "edges",
    "maxZoom",
    "minZoom",
    "translateExtent",
    "hooks",
    "defaultEdgeOptions"
  ];
  function useActions(state, nodeLookup, edgeLookup) {
    const viewportHelper = useViewportHelper(state);
    const updateNodeInternals = (ids) => {
      const updateIds = ids ?? [];
      state.hooks.updateNodeInternals.trigger(updateIds);
    };
    const getIncomers$1 = (nodeOrId) => {
      return getIncomers(nodeOrId, state.nodes, state.edges);
    };
    const getOutgoers$1 = (nodeOrId) => {
      return getOutgoers(nodeOrId, state.nodes, state.edges);
    };
    const getConnectedEdges$1 = (nodesOrId) => {
      return getConnectedEdges(nodesOrId, state.edges);
    };
    const getHandleConnections = ({ id: id2, type, nodeId }) => {
      var _a;
      const handleSuffix = id2 ? `-${type}-${id2}` : `-${type}`;
      return Array.from(((_a = state.connectionLookup.get(`${nodeId}${handleSuffix}`)) == null ? void 0 : _a.values()) ?? []);
    };
    const findNode = (id2) => {
      if (!id2) {
        return;
      }
      return nodeLookup.value.get(id2);
    };
    const findEdge = (id2) => {
      if (!id2) {
        return;
      }
      return edgeLookup.value.get(id2);
    };
    const updateNodePositions = (dragItems, changed, dragging) => {
      var _a, _b;
      const changes = [];
      for (const node of dragItems) {
        const change = {
          id: node.id,
          type: "position",
          dragging,
          from: node.from
        };
        if (changed) {
          change.position = node.position;
          if (node.parentNode) {
            const parentNode = findNode(node.parentNode);
            change.position = {
              x: change.position.x - (((_a = parentNode == null ? void 0 : parentNode.computedPosition) == null ? void 0 : _a.x) ?? 0),
              y: change.position.y - (((_b = parentNode == null ? void 0 : parentNode.computedPosition) == null ? void 0 : _b.y) ?? 0)
            };
          }
        }
        changes.push(change);
      }
      if (changes == null ? void 0 : changes.length) {
        state.hooks.nodesChange.trigger(changes);
      }
    };
    const updateNodeDimensions = (updates) => {
      if (!state.vueFlowRef) {
        return;
      }
      const viewportNode = state.vueFlowRef.querySelector(".vue-flow__transformationpane");
      if (!viewportNode) {
        return;
      }
      const style = window.getComputedStyle(viewportNode);
      const { m22: zoom2 } = new window.DOMMatrixReadOnly(style.transform);
      const changes = [];
      for (const element of updates) {
        const update = element;
        const node = findNode(update.id);
        if (node) {
          const dimensions = getDimensions(update.nodeElement);
          const doUpdate = !!(dimensions.width && dimensions.height && (node.dimensions.width !== dimensions.width || node.dimensions.height !== dimensions.height || update.forceUpdate));
          if (doUpdate) {
            const nodeBounds = update.nodeElement.getBoundingClientRect();
            node.dimensions = dimensions;
            node.handleBounds.source = getHandleBounds("source", update.nodeElement, nodeBounds, zoom2, node.id);
            node.handleBounds.target = getHandleBounds("target", update.nodeElement, nodeBounds, zoom2, node.id);
            changes.push({
              id: node.id,
              type: "dimensions",
              dimensions
            });
          }
        }
      }
      if (!state.fitViewOnInitDone && state.fitViewOnInit) {
        viewportHelper.value.fitView().then(() => {
          state.fitViewOnInitDone = true;
        });
      }
      if (changes.length) {
        state.hooks.nodesChange.trigger(changes);
      }
    };
    const elementSelectionHandler = (elements, selected) => {
      const nodeIds = /* @__PURE__ */ new Set();
      const edgeIds = /* @__PURE__ */ new Set();
      for (const element of elements) {
        if (isNode(element)) {
          nodeIds.add(element.id);
        } else if (isEdge(element)) {
          edgeIds.add(element.id);
        }
      }
      const changedNodes = getSelectionChanges(nodeLookup.value, nodeIds, true);
      const changedEdges = getSelectionChanges(edgeLookup.value, edgeIds);
      if (state.multiSelectionActive) {
        for (const nodeId of nodeIds) {
          changedNodes.push(createSelectionChange(nodeId, selected));
        }
        for (const edgeId of edgeIds) {
          changedEdges.push(createSelectionChange(edgeId, selected));
        }
      }
      if (changedNodes.length) {
        state.hooks.nodesChange.trigger(changedNodes);
      }
      if (changedEdges.length) {
        state.hooks.edgesChange.trigger(changedEdges);
      }
    };
    const addSelectedNodes = (nodes) => {
      if (state.multiSelectionActive) {
        const nodeChanges = nodes.map((node) => createSelectionChange(node.id, true));
        state.hooks.nodesChange.trigger(nodeChanges);
        return;
      }
      state.hooks.nodesChange.trigger(getSelectionChanges(nodeLookup.value, new Set(nodes.map((n) => n.id)), true));
      state.hooks.edgesChange.trigger(getSelectionChanges(edgeLookup.value));
    };
    const addSelectedEdges = (edges) => {
      if (state.multiSelectionActive) {
        const changedEdges = edges.map((edge) => createSelectionChange(edge.id, true));
        state.hooks.edgesChange.trigger(changedEdges);
        return;
      }
      state.hooks.edgesChange.trigger(getSelectionChanges(edgeLookup.value, new Set(edges.map((e) => e.id))));
      state.hooks.nodesChange.trigger(getSelectionChanges(nodeLookup.value, /* @__PURE__ */ new Set(), true));
    };
    const addSelectedElements = (elements) => {
      elementSelectionHandler(elements, true);
    };
    const removeSelectedNodes = (nodes) => {
      const nodesToUnselect = nodes || state.nodes;
      const nodeChanges = nodesToUnselect.map((n) => {
        n.selected = false;
        return createSelectionChange(n.id, false);
      });
      state.hooks.nodesChange.trigger(nodeChanges);
    };
    const removeSelectedEdges = (edges) => {
      const edgesToUnselect = edges || state.edges;
      const edgeChanges = edgesToUnselect.map((e) => {
        e.selected = false;
        return createSelectionChange(e.id, false);
      });
      state.hooks.edgesChange.trigger(edgeChanges);
    };
    const removeSelectedElements = (elements) => {
      if (!elements || !elements.length) {
        return elementSelectionHandler([], false);
      }
      const changes = elements.reduce(
        (changes2, curr) => {
          const selectionChange = createSelectionChange(curr.id, false);
          if (isNode(curr)) {
            changes2.nodes.push(selectionChange);
          } else {
            changes2.edges.push(selectionChange);
          }
          return changes2;
        },
        { nodes: [], edges: [] }
      );
      if (changes.nodes.length) {
        state.hooks.nodesChange.trigger(changes.nodes);
      }
      if (changes.edges.length) {
        state.hooks.edgesChange.trigger(changes.edges);
      }
    };
    const setMinZoom = (minZoom) => {
      var _a;
      (_a = state.d3Zoom) == null ? void 0 : _a.scaleExtent([minZoom, state.maxZoom]);
      state.minZoom = minZoom;
    };
    const setMaxZoom = (maxZoom) => {
      var _a;
      (_a = state.d3Zoom) == null ? void 0 : _a.scaleExtent([state.minZoom, maxZoom]);
      state.maxZoom = maxZoom;
    };
    const setTranslateExtent = (translateExtent) => {
      var _a;
      (_a = state.d3Zoom) == null ? void 0 : _a.translateExtent(translateExtent);
      state.translateExtent = translateExtent;
    };
    const setNodeExtent = (nodeExtent) => {
      state.nodeExtent = nodeExtent;
      updateNodeInternals();
    };
    const setPaneClickDistance = (clickDistance) => {
      var _a;
      (_a = state.d3Zoom) == null ? void 0 : _a.clickDistance(clickDistance);
    };
    const setInteractive = (isInteractive) => {
      state.nodesDraggable = isInteractive;
      state.nodesConnectable = isInteractive;
      state.elementsSelectable = isInteractive;
    };
    const setNodes = (nodes) => {
      const nextNodes = nodes instanceof Function ? nodes(state.nodes) : nodes;
      if (!state.initialized && !nextNodes.length) {
        return;
      }
      state.nodes = createGraphNodes(nextNodes, findNode, state.hooks.error.trigger);
    };
    const setEdges = (edges) => {
      const nextEdges = edges instanceof Function ? edges(state.edges) : edges;
      if (!state.initialized && !nextEdges.length) {
        return;
      }
      const validEdges = createGraphEdges(
        nextEdges,
        state.isValidConnection,
        findNode,
        findEdge,
        state.hooks.error.trigger,
        state.defaultEdgeOptions,
        state.nodes,
        state.edges
      );
      updateConnectionLookup(state.connectionLookup, edgeLookup.value, validEdges);
      state.edges = validEdges;
    };
    const setElements = (elements) => {
      const nextElements = elements instanceof Function ? elements([...state.nodes, ...state.edges]) : elements;
      if (!state.initialized && !nextElements.length) {
        return;
      }
      setNodes(nextElements.filter(isNode));
      setEdges(nextElements.filter(isEdge));
    };
    const addNodes = (nodes) => {
      let nextNodes = nodes instanceof Function ? nodes(state.nodes) : nodes;
      nextNodes = Array.isArray(nextNodes) ? nextNodes : [nextNodes];
      const graphNodes = createGraphNodes(nextNodes, findNode, state.hooks.error.trigger);
      const changes = [];
      for (const node of graphNodes) {
        changes.push(createAdditionChange(node));
      }
      if (changes.length) {
        state.hooks.nodesChange.trigger(changes);
      }
    };
    const addEdges = (params) => {
      let nextEdges = params instanceof Function ? params(state.edges) : params;
      nextEdges = Array.isArray(nextEdges) ? nextEdges : [nextEdges];
      const validEdges = createGraphEdges(
        nextEdges,
        state.isValidConnection,
        findNode,
        findEdge,
        state.hooks.error.trigger,
        state.defaultEdgeOptions,
        state.nodes,
        state.edges
      );
      const changes = [];
      for (const edge of validEdges) {
        changes.push(createAdditionChange(edge));
      }
      if (changes.length) {
        state.hooks.edgesChange.trigger(changes);
      }
    };
    const removeNodes = (nodes, removeConnectedEdges = true, removeChildren = false) => {
      const nextNodes = nodes instanceof Function ? nodes(state.nodes) : nodes;
      const nodesToRemove = Array.isArray(nextNodes) ? nextNodes : [nextNodes];
      const nodeChanges = [];
      const edgeChanges = [];
      function createEdgeRemovalChanges(nodes2) {
        const connectedEdges = getConnectedEdges$1(nodes2);
        for (const edge of connectedEdges) {
          if (isDef(edge.deletable) ? edge.deletable : true) {
            edgeChanges.push(createEdgeRemoveChange(edge.id, edge.source, edge.target, edge.sourceHandle, edge.targetHandle));
          }
        }
      }
      function createChildrenRemovalChanges(id2) {
        const children2 = [];
        for (const node of state.nodes) {
          if (node.parentNode === id2) {
            children2.push(node);
          }
        }
        if (children2.length) {
          for (const child of children2) {
            nodeChanges.push(createNodeRemoveChange(child.id));
          }
          if (removeConnectedEdges) {
            createEdgeRemovalChanges(children2);
          }
          for (const child of children2) {
            createChildrenRemovalChanges(child.id);
          }
        }
      }
      for (const item of nodesToRemove) {
        const currNode = typeof item === "string" ? findNode(item) : item;
        if (!currNode) {
          continue;
        }
        if (isDef(currNode.deletable) && !currNode.deletable) {
          continue;
        }
        nodeChanges.push(createNodeRemoveChange(currNode.id));
        if (removeConnectedEdges) {
          createEdgeRemovalChanges([currNode]);
        }
        if (removeChildren) {
          createChildrenRemovalChanges(currNode.id);
        }
      }
      if (edgeChanges.length) {
        state.hooks.edgesChange.trigger(edgeChanges);
      }
      if (nodeChanges.length) {
        state.hooks.nodesChange.trigger(nodeChanges);
      }
    };
    const removeEdges = (edges) => {
      const nextEdges = edges instanceof Function ? edges(state.edges) : edges;
      const edgesToRemove = Array.isArray(nextEdges) ? nextEdges : [nextEdges];
      const changes = [];
      for (const item of edgesToRemove) {
        const currEdge = typeof item === "string" ? findEdge(item) : item;
        if (!currEdge) {
          continue;
        }
        if (isDef(currEdge.deletable) && !currEdge.deletable) {
          continue;
        }
        changes.push(
          createEdgeRemoveChange(
            typeof item === "string" ? item : item.id,
            currEdge.source,
            currEdge.target,
            currEdge.sourceHandle,
            currEdge.targetHandle
          )
        );
      }
      state.hooks.edgesChange.trigger(changes);
    };
    const updateEdge2 = (oldEdge, newConnection, shouldReplaceId = true) => {
      const prevEdge = findEdge(oldEdge.id);
      if (!prevEdge) {
        return false;
      }
      const prevEdgeIndex = state.edges.indexOf(prevEdge);
      const newEdge = updateEdgeAction(oldEdge, newConnection, prevEdge, shouldReplaceId, state.hooks.error.trigger);
      if (newEdge) {
        const [validEdge] = createGraphEdges(
          [newEdge],
          state.isValidConnection,
          findNode,
          findEdge,
          state.hooks.error.trigger,
          state.defaultEdgeOptions,
          state.nodes,
          state.edges
        );
        state.edges = state.edges.map((edge, index) => index === prevEdgeIndex ? validEdge : edge);
        updateConnectionLookup(state.connectionLookup, edgeLookup.value, [validEdge]);
        return validEdge;
      }
      return false;
    };
    const updateEdgeData = (id2, dataUpdate, options = { replace: false }) => {
      const edge = findEdge(id2);
      if (!edge) {
        return;
      }
      const nextData = typeof dataUpdate === "function" ? dataUpdate(edge) : dataUpdate;
      edge.data = options.replace ? nextData : { ...edge.data, ...nextData };
    };
    const applyNodeChanges2 = (changes) => {
      return applyChanges(changes, state.nodes);
    };
    const applyEdgeChanges2 = (changes) => {
      const changedEdges = applyChanges(changes, state.edges);
      updateConnectionLookup(state.connectionLookup, edgeLookup.value, changedEdges);
      return changedEdges;
    };
    const updateNode = (id2, nodeUpdate, options = { replace: false }) => {
      const node = findNode(id2);
      if (!node) {
        return;
      }
      const nextNode = typeof nodeUpdate === "function" ? nodeUpdate(node) : nodeUpdate;
      if (options.replace) {
        state.nodes.splice(state.nodes.indexOf(node), 1, nextNode);
      } else {
        Object.assign(node, nextNode);
      }
    };
    const updateNodeData = (id2, dataUpdate, options = { replace: false }) => {
      const node = findNode(id2);
      if (!node) {
        return;
      }
      const nextData = typeof dataUpdate === "function" ? dataUpdate(node) : dataUpdate;
      node.data = options.replace ? nextData : { ...node.data, ...nextData };
    };
    const startConnection = (startHandle, position, isClick = false) => {
      if (isClick) {
        state.connectionClickStartHandle = startHandle;
      } else {
        state.connectionStartHandle = startHandle;
      }
      state.connectionEndHandle = null;
      state.connectionStatus = null;
      if (position) {
        state.connectionPosition = position;
      }
    };
    const updateConnection = (position, result = null, status = null) => {
      if (state.connectionStartHandle) {
        state.connectionPosition = position;
        state.connectionEndHandle = result;
        state.connectionStatus = status;
      }
    };
    const endConnection = (event, isClick) => {
      state.connectionPosition = { x: Number.NaN, y: Number.NaN };
      state.connectionEndHandle = null;
      state.connectionStatus = null;
      if (isClick) {
        state.connectionClickStartHandle = null;
      } else {
        state.connectionStartHandle = null;
      }
    };
    const getNodeRect = (nodeOrRect) => {
      const isRectObj = isRect(nodeOrRect);
      const node = isRectObj ? null : isGraphNode(nodeOrRect) ? nodeOrRect : findNode(nodeOrRect.id);
      if (!isRectObj && !node) {
        return [null, null, isRectObj];
      }
      const nodeRect = isRectObj ? nodeOrRect : nodeToRect(node);
      return [nodeRect, node, isRectObj];
    };
    const getIntersectingNodes = (nodeOrRect, partially = true, nodes = state.nodes) => {
      const [nodeRect, node, isRect2] = getNodeRect(nodeOrRect);
      if (!nodeRect) {
        return [];
      }
      const intersections = [];
      for (const n of nodes || state.nodes) {
        if (!isRect2 && (n.id === node.id || !n.computedPosition)) {
          continue;
        }
        const currNodeRect = nodeToRect(n);
        const overlappingArea = getOverlappingArea(currNodeRect, nodeRect);
        const partiallyVisible = partially && overlappingArea > 0;
        if (partiallyVisible || overlappingArea >= currNodeRect.width * currNodeRect.height || overlappingArea >= Number(nodeRect.width) * Number(nodeRect.height)) {
          intersections.push(n);
        }
      }
      return intersections;
    };
    const isNodeIntersecting = (nodeOrRect, area, partially = true) => {
      const [nodeRect] = getNodeRect(nodeOrRect);
      if (!nodeRect) {
        return false;
      }
      const overlappingArea = getOverlappingArea(nodeRect, area);
      const partiallyVisible = partially && overlappingArea > 0;
      return partiallyVisible || overlappingArea >= Number(nodeRect.width) * Number(nodeRect.height);
    };
    const panBy = (delta) => {
      const { viewport, dimensions, d3Zoom, d3Selection, translateExtent } = state;
      if (!d3Zoom || !d3Selection || !delta.x && !delta.y) {
        return false;
      }
      const nextTransform = identity.translate(viewport.x + delta.x, viewport.y + delta.y).scale(viewport.zoom);
      const extent = [
        [0, 0],
        [dimensions.width, dimensions.height]
      ];
      const constrainedTransform = d3Zoom.constrain()(nextTransform, extent, translateExtent);
      const transformChanged = state.viewport.x !== constrainedTransform.x || state.viewport.y !== constrainedTransform.y || state.viewport.zoom !== constrainedTransform.k;
      d3Zoom.transform(d3Selection, constrainedTransform);
      return transformChanged;
    };
    const setState = (options) => {
      const opts = options instanceof Function ? options(state) : options;
      const exclude = [
        "d3Zoom",
        "d3Selection",
        "d3ZoomHandler",
        "viewportRef",
        "vueFlowRef",
        "dimensions",
        "hooks"
      ];
      if (isDef(opts.defaultEdgeOptions)) {
        state.defaultEdgeOptions = opts.defaultEdgeOptions;
      }
      const elements = opts.modelValue || opts.nodes || opts.edges ? [] : void 0;
      if (elements) {
        if (opts.modelValue) {
          elements.push(...opts.modelValue);
        }
        if (opts.nodes) {
          elements.push(...opts.nodes);
        }
        if (opts.edges) {
          elements.push(...opts.edges);
        }
        setElements(elements);
      }
      const setSkippedOptions = () => {
        if (isDef(opts.maxZoom)) {
          setMaxZoom(opts.maxZoom);
        }
        if (isDef(opts.minZoom)) {
          setMinZoom(opts.minZoom);
        }
        if (isDef(opts.translateExtent)) {
          setTranslateExtent(opts.translateExtent);
        }
      };
      for (const o of Object.keys(opts)) {
        const key = o;
        const option = opts[key];
        if (![...storeOptionsToSkip, ...exclude].includes(key) && isDef(option)) {
          state[key] = option;
        }
      }
      until(() => state.d3Zoom).not.toBeNull().then(setSkippedOptions);
      if (!state.initialized) {
        state.initialized = true;
      }
    };
    const toObject = () => {
      const nodes = [];
      const edges = [];
      for (const node of state.nodes) {
        const {
          computedPosition: _,
          handleBounds: __,
          selected: ___,
          dimensions: ____,
          isParent: _____,
          resizing: ______,
          dragging: _______,
          events: _________,
          ...rest
        } = node;
        nodes.push(rest);
      }
      for (const edge of state.edges) {
        const { selected: _, sourceNode: __, targetNode: ___, events: ____, ...rest } = edge;
        edges.push(rest);
      }
      return JSON.parse(
        JSON.stringify({
          nodes,
          edges,
          position: [state.viewport.x, state.viewport.y],
          zoom: state.viewport.zoom,
          viewport: state.viewport
        })
      );
    };
    const fromObject = (obj) => {
      return new Promise((resolve2) => {
        const { nodes, edges, position, zoom: zoom2, viewport } = obj;
        if (nodes) {
          setNodes(nodes);
        }
        if (edges) {
          setEdges(edges);
        }
        const [xPos, yPos] = (viewport == null ? void 0 : viewport.x) && (viewport == null ? void 0 : viewport.y) ? [viewport.x, viewport.y] : position ?? [null, null];
        if (xPos && yPos) {
          const nextZoom = (viewport == null ? void 0 : viewport.zoom) || zoom2 || state.viewport.zoom;
          return until(() => viewportHelper.value.viewportInitialized).toBe(true).then(() => {
            viewportHelper.value.setViewport({
              x: xPos,
              y: yPos,
              zoom: nextZoom
            }).then(() => {
              resolve2(true);
            });
          });
        } else {
          resolve2(true);
        }
      });
    };
    const $reset = () => {
      const resetState = useState();
      state.edges = [];
      state.nodes = [];
      if (state.d3Zoom && state.d3Selection) {
        const updatedTransform = identity.translate(resetState.defaultViewport.x ?? 0, resetState.defaultViewport.y ?? 0).scale(clamp(resetState.defaultViewport.zoom ?? 1, resetState.minZoom, resetState.maxZoom));
        const bbox = state.viewportRef.getBoundingClientRect();
        const extent = [
          [0, 0],
          [bbox.width, bbox.height]
        ];
        const constrainedTransform = state.d3Zoom.constrain()(updatedTransform, extent, resetState.translateExtent);
        state.d3Zoom.transform(state.d3Selection, constrainedTransform);
      }
      setState(resetState);
    };
    return {
      updateNodePositions,
      updateNodeDimensions,
      setElements,
      setNodes,
      setEdges,
      addNodes,
      addEdges,
      removeNodes,
      removeEdges,
      findNode,
      findEdge,
      updateEdge: updateEdge2,
      updateEdgeData,
      updateNode,
      updateNodeData,
      applyEdgeChanges: applyEdgeChanges2,
      applyNodeChanges: applyNodeChanges2,
      addSelectedElements,
      addSelectedNodes,
      addSelectedEdges,
      setMinZoom,
      setMaxZoom,
      setTranslateExtent,
      setNodeExtent,
      setPaneClickDistance,
      removeSelectedElements,
      removeSelectedNodes,
      removeSelectedEdges,
      startConnection,
      updateConnection,
      endConnection,
      setInteractive,
      setState,
      getIntersectingNodes,
      getIncomers: getIncomers$1,
      getOutgoers: getOutgoers$1,
      getConnectedEdges: getConnectedEdges$1,
      getHandleConnections,
      isNodeIntersecting,
      panBy,
      fitView: (params) => viewportHelper.value.fitView(params),
      zoomIn: (transitionOpts) => viewportHelper.value.zoomIn(transitionOpts),
      zoomOut: (transitionOpts) => viewportHelper.value.zoomOut(transitionOpts),
      zoomTo: (zoomLevel, transitionOpts) => viewportHelper.value.zoomTo(zoomLevel, transitionOpts),
      setViewport: (params, transitionOpts) => viewportHelper.value.setViewport(params, transitionOpts),
      setTransform: (params, transitionOpts) => viewportHelper.value.setTransform(params, transitionOpts),
      getViewport: () => viewportHelper.value.getViewport(),
      getTransform: () => viewportHelper.value.getTransform(),
      setCenter: (x, y, opts) => viewportHelper.value.setCenter(x, y, opts),
      fitBounds: (params, opts) => viewportHelper.value.fitBounds(params, opts),
      project: (params) => viewportHelper.value.project(params),
      screenToFlowCoordinate: (params) => viewportHelper.value.screenToFlowCoordinate(params),
      flowToScreenCoordinate: (params) => viewportHelper.value.flowToScreenCoordinate(params),
      toObject,
      fromObject,
      updateNodeInternals,
      viewportHelper,
      $reset,
      $destroy: () => {
      }
    };
  }
  var _hoisted_1$9 = ["data-id", "data-handleid", "data-nodeid", "data-handlepos"];
  var __default__$f = {
    name: "Handle",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$f = /* @__PURE__ */ defineComponent({
    ...__default__$f,
    props: {
      id: { default: null },
      type: {},
      position: { default: () => Position.Top },
      isValidConnection: { type: Function },
      connectable: { type: [Boolean, Number, String, Function], default: void 0 },
      connectableStart: { type: Boolean, default: true },
      connectableEnd: { type: Boolean, default: true }
    },
    setup(__props, { expose: __expose }) {
      const props = createPropsRestProxy(__props, ["position", "connectable", "connectableStart", "connectableEnd", "id"]);
      const type = toRef(() => props.type ?? "source");
      const isValidConnection = toRef(() => props.isValidConnection ?? null);
      const {
        id: flowId,
        connectionStartHandle,
        connectionClickStartHandle,
        connectionEndHandle,
        vueFlowRef,
        nodesConnectable,
        noDragClassName,
        noPanClassName
      } = useVueFlow();
      const { id: nodeId, node, nodeEl, connectedEdges } = useNode();
      const handle = ref();
      const isConnectableStart = toRef(() => typeof __props.connectableStart !== "undefined" ? __props.connectableStart : true);
      const isConnectableEnd = toRef(() => typeof __props.connectableEnd !== "undefined" ? __props.connectableEnd : true);
      const isConnecting = toRef(
        () => {
          var _a, _b, _c, _d, _e, _f;
          return ((_a = connectionStartHandle.value) == null ? void 0 : _a.nodeId) === nodeId && ((_b = connectionStartHandle.value) == null ? void 0 : _b.id) === __props.id && ((_c = connectionStartHandle.value) == null ? void 0 : _c.type) === type.value || ((_d = connectionEndHandle.value) == null ? void 0 : _d.nodeId) === nodeId && ((_e = connectionEndHandle.value) == null ? void 0 : _e.id) === __props.id && ((_f = connectionEndHandle.value) == null ? void 0 : _f.type) === type.value;
        }
      );
      const isClickConnecting = toRef(
        () => {
          var _a, _b, _c;
          return ((_a = connectionClickStartHandle.value) == null ? void 0 : _a.nodeId) === nodeId && ((_b = connectionClickStartHandle.value) == null ? void 0 : _b.id) === __props.id && ((_c = connectionClickStartHandle.value) == null ? void 0 : _c.type) === type.value;
        }
      );
      const { handlePointerDown, handleClick } = useHandle({
        nodeId,
        handleId: __props.id,
        isValidConnection,
        type
      });
      const isConnectable = computed2(() => {
        if (typeof __props.connectable === "string" && __props.connectable === "single") {
          return !connectedEdges.value.some((edge) => {
            const id2 = edge[`${type.value}Handle`];
            if (edge[type.value] !== nodeId) {
              return false;
            }
            return id2 ? id2 === __props.id : true;
          });
        }
        if (typeof __props.connectable === "number") {
          return connectedEdges.value.filter((edge) => {
            const id2 = edge[`${type.value}Handle`];
            if (edge[type.value] !== nodeId) {
              return false;
            }
            return id2 ? id2 === __props.id : true;
          }).length < __props.connectable;
        }
        if (typeof __props.connectable === "function") {
          return __props.connectable(node, connectedEdges.value);
        }
        return isDef(__props.connectable) ? __props.connectable : nodesConnectable.value;
      });
      onMounted(() => {
        var _a;
        if (!node.dimensions.width || !node.dimensions.height) {
          return;
        }
        const existingBounds = (_a = node.handleBounds[type.value]) == null ? void 0 : _a.find((b) => b.id === __props.id);
        if (!vueFlowRef.value || existingBounds) {
          return;
        }
        const viewportNode = vueFlowRef.value.querySelector(".vue-flow__transformationpane");
        if (!nodeEl.value || !handle.value || !viewportNode || !__props.id) {
          return;
        }
        const nodeBounds = nodeEl.value.getBoundingClientRect();
        const handleBounds = handle.value.getBoundingClientRect();
        const style = window.getComputedStyle(viewportNode);
        const { m22: zoom2 } = new window.DOMMatrixReadOnly(style.transform);
        const nextBounds = {
          id: __props.id,
          position: __props.position,
          x: (handleBounds.left - nodeBounds.left) / zoom2,
          y: (handleBounds.top - nodeBounds.top) / zoom2,
          type: type.value,
          nodeId,
          ...getDimensions(handle.value)
        };
        node.handleBounds[type.value] = [...node.handleBounds[type.value] ?? [], nextBounds];
      });
      function onPointerDown(event) {
        const isMouseTriggered = isMouseEvent(event);
        if (isConnectable.value && isConnectableStart.value && (isMouseTriggered && event.button === 0 || !isMouseTriggered)) {
          handlePointerDown(event);
        }
      }
      function onClick(event) {
        if (!nodeId || !connectionClickStartHandle.value && !isConnectableStart.value) {
          return;
        }
        if (isConnectable.value) {
          handleClick(event);
        }
      }
      __expose({
        handleClick,
        handlePointerDown,
        onClick,
        onPointerDown
      });
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          ref_key: "handle",
          ref: handle,
          "data-id": `${unref(flowId)}-${unref(nodeId)}-${__props.id}-${type.value}`,
          "data-handleid": __props.id,
          "data-nodeid": unref(nodeId),
          "data-handlepos": _ctx.position,
          class: normalizeClass(["vue-flow__handle", [
            `vue-flow__handle-${_ctx.position}`,
            `vue-flow__handle-${__props.id}`,
            unref(noDragClassName),
            unref(noPanClassName),
            type.value,
            {
              connectable: isConnectable.value,
              connecting: isClickConnecting.value,
              connectablestart: isConnectableStart.value,
              connectableend: isConnectableEnd.value,
              connectionindicator: isConnectable.value && (isConnectableStart.value && !isConnecting.value || isConnectableEnd.value && isConnecting.value)
            }
          ]]),
          onMousedown: onPointerDown,
          onTouchstartPassive: onPointerDown,
          onClick
        }, [
          renderSlot(_ctx.$slots, "default", { id: _ctx.id })
        ], 42, _hoisted_1$9);
      };
    }
  });
  var DefaultNode = function({
    sourcePosition = Position.Bottom,
    targetPosition = Position.Top,
    label: _label,
    connectable = true,
    isValidTargetPos,
    isValidSourcePos,
    data
  }) {
    const label = data.label ?? _label;
    return [
      h(_sfc_main$f, { type: "target", position: targetPosition, connectable, isValidConnection: isValidTargetPos }),
      typeof label !== "string" && label ? h(label) : h(Fragment, [label]),
      h(_sfc_main$f, { type: "source", position: sourcePosition, connectable, isValidConnection: isValidSourcePos })
    ];
  };
  DefaultNode.props = ["sourcePosition", "targetPosition", "label", "isValidTargetPos", "isValidSourcePos", "connectable", "data"];
  DefaultNode.inheritAttrs = false;
  DefaultNode.compatConfig = { MODE: 3 };
  var DefaultNode$1 = DefaultNode;
  var OutputNode = function({
    targetPosition = Position.Top,
    label: _label,
    connectable = true,
    isValidTargetPos,
    data
  }) {
    const label = data.label ?? _label;
    return [
      h(_sfc_main$f, { type: "target", position: targetPosition, connectable, isValidConnection: isValidTargetPos }),
      typeof label !== "string" && label ? h(label) : h(Fragment, [label])
    ];
  };
  OutputNode.props = ["targetPosition", "label", "isValidTargetPos", "connectable", "data"];
  OutputNode.inheritAttrs = false;
  OutputNode.compatConfig = { MODE: 3 };
  var OutputNode$1 = OutputNode;
  var InputNode = function({
    sourcePosition = Position.Bottom,
    label: _label,
    connectable = true,
    isValidSourcePos,
    data
  }) {
    const label = data.label ?? _label;
    return [
      typeof label !== "string" && label ? h(label) : h(Fragment, [label]),
      h(_sfc_main$f, { type: "source", position: sourcePosition, connectable, isValidConnection: isValidSourcePos })
    ];
  };
  InputNode.props = ["sourcePosition", "label", "isValidSourcePos", "connectable", "data"];
  InputNode.inheritAttrs = false;
  InputNode.compatConfig = { MODE: 3 };
  var InputNode$1 = InputNode;
  var _hoisted_1$8 = ["transform"];
  var _hoisted_2$2 = ["width", "height", "x", "y", "rx", "ry"];
  var _hoisted_3$1 = ["y"];
  var __default__$e = {
    name: "EdgeText",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$e = /* @__PURE__ */ defineComponent({
    ...__default__$e,
    props: {
      x: {},
      y: {},
      label: {},
      labelStyle: { default: () => ({}) },
      labelShowBg: { type: Boolean, default: true },
      labelBgStyle: { default: () => ({}) },
      labelBgPadding: { default: () => [2, 4] },
      labelBgBorderRadius: { default: 2 }
    },
    setup(__props) {
      const box = ref({ x: 0, y: 0, width: 0, height: 0 });
      const el = ref(null);
      const transform = computed2(() => `translate(${__props.x - box.value.width / 2} ${__props.y - box.value.height / 2})`);
      onMounted(getBox);
      watch2([() => __props.x, () => __props.y, el, () => __props.label], getBox);
      function getBox() {
        if (!el.value) {
          return;
        }
        const nextBox = el.value.getBBox();
        if (nextBox.width !== box.value.width || nextBox.height !== box.value.height) {
          box.value = nextBox;
        }
      }
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("g", {
          transform: transform.value,
          class: "vue-flow__edge-textwrapper"
        }, [
          _ctx.labelShowBg ? (openBlock(), createElementBlock("rect", {
            key: 0,
            class: "vue-flow__edge-textbg",
            width: `${box.value.width + 2 * _ctx.labelBgPadding[0]}px`,
            height: `${box.value.height + 2 * _ctx.labelBgPadding[1]}px`,
            x: -_ctx.labelBgPadding[0],
            y: -_ctx.labelBgPadding[1],
            style: normalizeStyle(_ctx.labelBgStyle),
            rx: _ctx.labelBgBorderRadius,
            ry: _ctx.labelBgBorderRadius
          }, null, 12, _hoisted_2$2)) : createCommentVNode("", true),
          createBaseVNode("text", mergeProps(_ctx.$attrs, {
            ref_key: "el",
            ref: el,
            class: "vue-flow__edge-text",
            y: box.value.height / 2,
            dy: "0.3em",
            style: _ctx.labelStyle
          }), [
            renderSlot(_ctx.$slots, "default", {}, () => [
              typeof _ctx.label !== "string" ? (openBlock(), createBlock(resolveDynamicComponent(_ctx.label), { key: 0 })) : (openBlock(), createElementBlock(Fragment, { key: 1 }, [
                createTextVNode(toDisplayString(_ctx.label), 1)
              ], 64))
            ])
          ], 16, _hoisted_3$1)
        ], 8, _hoisted_1$8);
      };
    }
  });
  var _hoisted_1$7 = ["id", "d", "marker-end", "marker-start"];
  var _hoisted_2$1 = ["d", "stroke-width"];
  var __default__$d = {
    name: "BaseEdge",
    inheritAttrs: false,
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$d = /* @__PURE__ */ defineComponent({
    ...__default__$d,
    props: {
      id: {},
      labelX: {},
      labelY: {},
      path: {},
      label: {},
      markerStart: {},
      markerEnd: {},
      interactionWidth: { default: 20 },
      labelStyle: {},
      labelShowBg: { type: Boolean },
      labelBgStyle: {},
      labelBgPadding: {},
      labelBgBorderRadius: {}
    },
    setup(__props, { expose: __expose }) {
      const pathEl = ref(null);
      const interactionEl = ref(null);
      const labelEl = ref(null);
      const attrs = useAttrs();
      __expose({
        pathEl,
        interactionEl,
        labelEl
      });
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock(Fragment, null, [
          createBaseVNode("path", mergeProps(unref(attrs), {
            id: _ctx.id,
            ref_key: "pathEl",
            ref: pathEl,
            d: _ctx.path,
            class: "vue-flow__edge-path",
            "marker-end": _ctx.markerEnd,
            "marker-start": _ctx.markerStart
          }), null, 16, _hoisted_1$7),
          _ctx.interactionWidth ? (openBlock(), createElementBlock("path", {
            key: 0,
            ref_key: "interactionEl",
            ref: interactionEl,
            fill: "none",
            d: _ctx.path,
            "stroke-width": _ctx.interactionWidth,
            "stroke-opacity": 0,
            class: "vue-flow__edge-interaction"
          }, null, 8, _hoisted_2$1)) : createCommentVNode("", true),
          _ctx.label && _ctx.labelX && _ctx.labelY ? (openBlock(), createBlock(_sfc_main$e, {
            key: 1,
            ref_key: "labelEl",
            ref: labelEl,
            x: _ctx.labelX,
            y: _ctx.labelY,
            label: _ctx.label,
            "label-show-bg": _ctx.labelShowBg,
            "label-bg-style": _ctx.labelBgStyle,
            "label-bg-padding": _ctx.labelBgPadding,
            "label-bg-border-radius": _ctx.labelBgBorderRadius,
            "label-style": _ctx.labelStyle
          }, null, 8, ["x", "y", "label", "label-show-bg", "label-bg-style", "label-bg-padding", "label-bg-border-radius", "label-style"])) : createCommentVNode("", true)
        ], 64);
      };
    }
  });
  function getSimpleEdgeCenter({
    sourceX,
    sourceY,
    targetX,
    targetY
  }) {
    const xOffset = Math.abs(targetX - sourceX) / 2;
    const centerX = targetX < sourceX ? targetX + xOffset : targetX - xOffset;
    const yOffset = Math.abs(targetY - sourceY) / 2;
    const centerY = targetY < sourceY ? targetY + yOffset : targetY - yOffset;
    return [centerX, centerY, xOffset, yOffset];
  }
  function getBezierEdgeCenter({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourceControlX,
    sourceControlY,
    targetControlX,
    targetControlY
  }) {
    const centerX = sourceX * 0.125 + sourceControlX * 0.375 + targetControlX * 0.375 + targetX * 0.125;
    const centerY = sourceY * 0.125 + sourceControlY * 0.375 + targetControlY * 0.375 + targetY * 0.125;
    const offsetX = Math.abs(centerX - sourceX);
    const offsetY = Math.abs(centerY - sourceY);
    return [centerX, centerY, offsetX, offsetY];
  }
  function calculateControlOffset(distance2, curvature) {
    if (distance2 >= 0) {
      return 0.5 * distance2;
    } else {
      return curvature * 25 * Math.sqrt(-distance2);
    }
  }
  function getControlWithCurvature({ pos, x1, y1, x2, y2, c }) {
    let ctX, ctY;
    switch (pos) {
      case Position.Left:
        ctX = x1 - calculateControlOffset(x1 - x2, c);
        ctY = y1;
        break;
      case Position.Right:
        ctX = x1 + calculateControlOffset(x2 - x1, c);
        ctY = y1;
        break;
      case Position.Top:
        ctX = x1;
        ctY = y1 - calculateControlOffset(y1 - y2, c);
        break;
      case Position.Bottom:
        ctX = x1;
        ctY = y1 + calculateControlOffset(y2 - y1, c);
        break;
    }
    return [ctX, ctY];
  }
  function getBezierPath(bezierPathParams) {
    const {
      sourceX,
      sourceY,
      sourcePosition = Position.Bottom,
      targetX,
      targetY,
      targetPosition = Position.Top,
      curvature = 0.25
    } = bezierPathParams;
    const [sourceControlX, sourceControlY] = getControlWithCurvature({
      pos: sourcePosition,
      x1: sourceX,
      y1: sourceY,
      x2: targetX,
      y2: targetY,
      c: curvature
    });
    const [targetControlX, targetControlY] = getControlWithCurvature({
      pos: targetPosition,
      x1: targetX,
      y1: targetY,
      x2: sourceX,
      y2: sourceY,
      c: curvature
    });
    const [labelX, labelY, offsetX, offsetY] = getBezierEdgeCenter({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourceControlX,
      sourceControlY,
      targetControlX,
      targetControlY
    });
    return [
      `M${sourceX},${sourceY} C${sourceControlX},${sourceControlY} ${targetControlX},${targetControlY} ${targetX},${targetY}`,
      labelX,
      labelY,
      offsetX,
      offsetY
    ];
  }
  function getControl({ pos, x1, y1, x2, y2 }) {
    let ctX, ctY;
    switch (pos) {
      case Position.Left:
      case Position.Right:
        ctX = 0.5 * (x1 + x2);
        ctY = y1;
        break;
      case Position.Top:
      case Position.Bottom:
        ctX = x1;
        ctY = 0.5 * (y1 + y2);
        break;
    }
    return [ctX, ctY];
  }
  function getSimpleBezierPath(simpleBezierPathParams) {
    const {
      sourceX,
      sourceY,
      sourcePosition = Position.Bottom,
      targetX,
      targetY,
      targetPosition = Position.Top
    } = simpleBezierPathParams;
    const [sourceControlX, sourceControlY] = getControl({
      pos: sourcePosition,
      x1: sourceX,
      y1: sourceY,
      x2: targetX,
      y2: targetY
    });
    const [targetControlX, targetControlY] = getControl({
      pos: targetPosition,
      x1: targetX,
      y1: targetY,
      x2: sourceX,
      y2: sourceY
    });
    const [centerX, centerY, offsetX, offsetY] = getBezierEdgeCenter({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourceControlX,
      sourceControlY,
      targetControlX,
      targetControlY
    });
    return [
      `M${sourceX},${sourceY} C${sourceControlX},${sourceControlY} ${targetControlX},${targetControlY} ${targetX},${targetY}`,
      centerX,
      centerY,
      offsetX,
      offsetY
    ];
  }
  var handleDirections = {
    [Position.Left]: { x: -1, y: 0 },
    [Position.Right]: { x: 1, y: 0 },
    [Position.Top]: { x: 0, y: -1 },
    [Position.Bottom]: { x: 0, y: 1 }
  };
  function getDirection({
    source,
    sourcePosition = Position.Bottom,
    target
  }) {
    if (sourcePosition === Position.Left || sourcePosition === Position.Right) {
      return source.x < target.x ? { x: 1, y: 0 } : { x: -1, y: 0 };
    }
    return source.y < target.y ? { x: 0, y: 1 } : { x: 0, y: -1 };
  }
  function distance(a, b) {
    return Math.sqrt((b.x - a.x) ** 2 + (b.y - a.y) ** 2);
  }
  function getPoints({
    source,
    sourcePosition = Position.Bottom,
    target,
    targetPosition = Position.Top,
    center,
    offset
  }) {
    const sourceDir = handleDirections[sourcePosition];
    const targetDir = handleDirections[targetPosition];
    const sourceGapped = { x: source.x + sourceDir.x * offset, y: source.y + sourceDir.y * offset };
    const targetGapped = { x: target.x + targetDir.x * offset, y: target.y + targetDir.y * offset };
    const dir = getDirection({
      source: sourceGapped,
      sourcePosition,
      target: targetGapped
    });
    const dirAccessor = dir.x !== 0 ? "x" : "y";
    const currDir = dir[dirAccessor];
    let points;
    let centerX, centerY;
    const sourceGapOffset = { x: 0, y: 0 };
    const targetGapOffset = { x: 0, y: 0 };
    const [defaultCenterX, defaultCenterY, defaultOffsetX, defaultOffsetY] = getSimpleEdgeCenter({
      sourceX: source.x,
      sourceY: source.y,
      targetX: target.x,
      targetY: target.y
    });
    if (sourceDir[dirAccessor] * targetDir[dirAccessor] === -1) {
      centerX = center.x ?? defaultCenterX;
      centerY = center.y ?? defaultCenterY;
      const verticalSplit = [
        { x: centerX, y: sourceGapped.y },
        { x: centerX, y: targetGapped.y }
      ];
      const horizontalSplit = [
        { x: sourceGapped.x, y: centerY },
        { x: targetGapped.x, y: centerY }
      ];
      if (sourceDir[dirAccessor] === currDir) {
        points = dirAccessor === "x" ? verticalSplit : horizontalSplit;
      } else {
        points = dirAccessor === "x" ? horizontalSplit : verticalSplit;
      }
    } else {
      const sourceTarget = [{ x: sourceGapped.x, y: targetGapped.y }];
      const targetSource = [{ x: targetGapped.x, y: sourceGapped.y }];
      if (dirAccessor === "x") {
        points = sourceDir.x === currDir ? targetSource : sourceTarget;
      } else {
        points = sourceDir.y === currDir ? sourceTarget : targetSource;
      }
      if (sourcePosition === targetPosition) {
        const diff = Math.abs(source[dirAccessor] - target[dirAccessor]);
        if (diff <= offset) {
          const gapOffset = Math.min(offset - 1, offset - diff);
          if (sourceDir[dirAccessor] === currDir) {
            sourceGapOffset[dirAccessor] = (sourceGapped[dirAccessor] > source[dirAccessor] ? -1 : 1) * gapOffset;
          } else {
            targetGapOffset[dirAccessor] = (targetGapped[dirAccessor] > target[dirAccessor] ? -1 : 1) * gapOffset;
          }
        }
      }
      if (sourcePosition !== targetPosition) {
        const dirAccessorOpposite = dirAccessor === "x" ? "y" : "x";
        const isSameDir = sourceDir[dirAccessor] === targetDir[dirAccessorOpposite];
        const sourceGtTargetOppo = sourceGapped[dirAccessorOpposite] > targetGapped[dirAccessorOpposite];
        const sourceLtTargetOppo = sourceGapped[dirAccessorOpposite] < targetGapped[dirAccessorOpposite];
        const flipSourceTarget = sourceDir[dirAccessor] === 1 && (!isSameDir && sourceGtTargetOppo || isSameDir && sourceLtTargetOppo) || sourceDir[dirAccessor] !== 1 && (!isSameDir && sourceLtTargetOppo || isSameDir && sourceGtTargetOppo);
        if (flipSourceTarget) {
          points = dirAccessor === "x" ? sourceTarget : targetSource;
        }
      }
      const sourceGapPoint = { x: sourceGapped.x + sourceGapOffset.x, y: sourceGapped.y + sourceGapOffset.y };
      const targetGapPoint = { x: targetGapped.x + targetGapOffset.x, y: targetGapped.y + targetGapOffset.y };
      const maxXDistance = Math.max(Math.abs(sourceGapPoint.x - points[0].x), Math.abs(targetGapPoint.x - points[0].x));
      const maxYDistance = Math.max(Math.abs(sourceGapPoint.y - points[0].y), Math.abs(targetGapPoint.y - points[0].y));
      if (maxXDistance >= maxYDistance) {
        centerX = (sourceGapPoint.x + targetGapPoint.x) / 2;
        centerY = points[0].y;
      } else {
        centerX = points[0].x;
        centerY = (sourceGapPoint.y + targetGapPoint.y) / 2;
      }
    }
    const pathPoints = [
      source,
      { x: sourceGapped.x + sourceGapOffset.x, y: sourceGapped.y + sourceGapOffset.y },
      ...points,
      { x: targetGapped.x + targetGapOffset.x, y: targetGapped.y + targetGapOffset.y },
      target
    ];
    return [pathPoints, centerX, centerY, defaultOffsetX, defaultOffsetY];
  }
  function getBend(a, b, c, size) {
    const bendSize = Math.min(distance(a, b) / 2, distance(b, c) / 2, size);
    const { x, y } = b;
    if (a.x === x && x === c.x || a.y === y && y === c.y) {
      return `L${x} ${y}`;
    }
    if (a.y === y) {
      const xDir2 = a.x < c.x ? -1 : 1;
      const yDir2 = a.y < c.y ? 1 : -1;
      return `L ${x + bendSize * xDir2},${y}Q ${x},${y} ${x},${y + bendSize * yDir2}`;
    }
    const xDir = a.x < c.x ? 1 : -1;
    const yDir = a.y < c.y ? -1 : 1;
    return `L ${x},${y + bendSize * yDir}Q ${x},${y} ${x + bendSize * xDir},${y}`;
  }
  function getSmoothStepPath(smoothStepPathParams) {
    const {
      sourceX,
      sourceY,
      sourcePosition = Position.Bottom,
      targetX,
      targetY,
      targetPosition = Position.Top,
      borderRadius = 5,
      centerX,
      centerY,
      offset = 20
    } = smoothStepPathParams;
    const [points, labelX, labelY, offsetX, offsetY] = getPoints({
      source: { x: sourceX, y: sourceY },
      sourcePosition,
      target: { x: targetX, y: targetY },
      targetPosition,
      center: { x: centerX, y: centerY },
      offset
    });
    const path = points.reduce((res, p2, i) => {
      let segment;
      if (i > 0 && i < points.length - 1) {
        segment = getBend(points[i - 1], p2, points[i + 1], borderRadius);
      } else {
        segment = `${i === 0 ? "M" : "L"}${p2.x} ${p2.y}`;
      }
      res += segment;
      return res;
    }, "");
    return [path, labelX, labelY, offsetX, offsetY];
  }
  function getStraightPath(straightEdgeParams) {
    const { sourceX, sourceY, targetX, targetY } = straightEdgeParams;
    const [centerX, centerY, offsetX, offsetY] = getSimpleEdgeCenter({
      sourceX,
      sourceY,
      targetX,
      targetY
    });
    return [`M ${sourceX},${sourceY}L ${targetX},${targetY}`, centerX, centerY, offsetX, offsetY];
  }
  var StraightEdge = defineComponent({
    name: "StraightEdge",
    props: [
      "label",
      "labelStyle",
      "labelShowBg",
      "labelBgStyle",
      "labelBgPadding",
      "labelBgBorderRadius",
      "sourceY",
      "sourceX",
      "targetX",
      "targetY",
      "markerEnd",
      "markerStart",
      "interactionWidth"
    ],
    compatConfig: { MODE: 3 },
    setup(props, { attrs }) {
      return () => {
        const [path, labelX, labelY] = getStraightPath(props);
        return h(_sfc_main$d, {
          path,
          labelX,
          labelY,
          ...attrs,
          ...props
        });
      };
    }
  });
  var StraightEdge$1 = StraightEdge;
  var SmoothStepEdge = defineComponent({
    name: "SmoothStepEdge",
    props: [
      "sourcePosition",
      "targetPosition",
      "label",
      "labelStyle",
      "labelShowBg",
      "labelBgStyle",
      "labelBgPadding",
      "labelBgBorderRadius",
      "sourceY",
      "sourceX",
      "targetX",
      "targetY",
      "borderRadius",
      "markerEnd",
      "markerStart",
      "interactionWidth",
      "offset"
    ],
    compatConfig: { MODE: 3 },
    setup(props, { attrs }) {
      return () => {
        const [path, labelX, labelY] = getSmoothStepPath({
          ...props,
          sourcePosition: props.sourcePosition ?? Position.Bottom,
          targetPosition: props.targetPosition ?? Position.Top
        });
        return h(_sfc_main$d, {
          path,
          labelX,
          labelY,
          ...attrs,
          ...props
        });
      };
    }
  });
  var SmoothStepEdge$1 = SmoothStepEdge;
  var StepEdge = defineComponent({
    name: "StepEdge",
    props: [
      "sourcePosition",
      "targetPosition",
      "label",
      "labelStyle",
      "labelShowBg",
      "labelBgStyle",
      "labelBgPadding",
      "labelBgBorderRadius",
      "sourceY",
      "sourceX",
      "targetX",
      "targetY",
      "markerEnd",
      "markerStart",
      "interactionWidth"
    ],
    setup(props, { attrs }) {
      return () => h(SmoothStepEdge$1, { ...props, ...attrs, borderRadius: 0 });
    }
  });
  var StepEdge$1 = StepEdge;
  var BezierEdge = defineComponent({
    name: "BezierEdge",
    props: [
      "sourcePosition",
      "targetPosition",
      "label",
      "labelStyle",
      "labelShowBg",
      "labelBgStyle",
      "labelBgPadding",
      "labelBgBorderRadius",
      "sourceY",
      "sourceX",
      "targetX",
      "targetY",
      "curvature",
      "markerEnd",
      "markerStart",
      "interactionWidth"
    ],
    compatConfig: { MODE: 3 },
    setup(props, { attrs }) {
      return () => {
        const [path, labelX, labelY] = getBezierPath({
          ...props,
          sourcePosition: props.sourcePosition ?? Position.Bottom,
          targetPosition: props.targetPosition ?? Position.Top
        });
        return h(_sfc_main$d, {
          path,
          labelX,
          labelY,
          ...attrs,
          ...props
        });
      };
    }
  });
  var BezierEdge$1 = BezierEdge;
  var SimpleBezierEdge = defineComponent({
    name: "SimpleBezierEdge",
    props: [
      "sourcePosition",
      "targetPosition",
      "label",
      "labelStyle",
      "labelShowBg",
      "labelBgStyle",
      "labelBgPadding",
      "labelBgBorderRadius",
      "sourceY",
      "sourceX",
      "targetX",
      "targetY",
      "markerEnd",
      "markerStart",
      "interactionWidth"
    ],
    compatConfig: { MODE: 3 },
    setup(props, { attrs }) {
      return () => {
        const [path, labelX, labelY] = getSimpleBezierPath({
          ...props,
          sourcePosition: props.sourcePosition ?? Position.Bottom,
          targetPosition: props.targetPosition ?? Position.Top
        });
        return h(_sfc_main$d, {
          path,
          labelX,
          labelY,
          ...attrs,
          ...props
        });
      };
    }
  });
  var SimpleBezierEdge$1 = SimpleBezierEdge;
  var defaultNodeTypes = {
    input: InputNode$1,
    default: DefaultNode$1,
    output: OutputNode$1
  };
  var defaultEdgeTypes = {
    default: BezierEdge$1,
    straight: StraightEdge$1,
    step: StepEdge$1,
    smoothstep: SmoothStepEdge$1,
    simplebezier: SimpleBezierEdge$1
  };
  function useGetters(state, nodeLookup, edgeLookup) {
    const getNode = computed2(() => (id2) => nodeLookup.value.get(id2));
    const getEdge = computed2(() => (id2) => edgeLookup.value.get(id2));
    const getEdgeTypes = computed2(() => {
      const edgeTypes = {
        ...defaultEdgeTypes,
        ...state.edgeTypes
      };
      const keys = Object.keys(edgeTypes);
      for (const e of state.edges) {
        e.type && !keys.includes(e.type) && (edgeTypes[e.type] = e.type);
      }
      return edgeTypes;
    });
    const getNodeTypes = computed2(() => {
      const nodeTypes = {
        ...defaultNodeTypes,
        ...state.nodeTypes
      };
      const keys = Object.keys(nodeTypes);
      for (const n of state.nodes) {
        n.type && !keys.includes(n.type) && (nodeTypes[n.type] = n.type);
      }
      return nodeTypes;
    });
    const getNodes = computed2(() => {
      if (state.onlyRenderVisibleElements) {
        return getNodesInside(
          state.nodes,
          {
            x: 0,
            y: 0,
            width: state.dimensions.width,
            height: state.dimensions.height
          },
          state.viewport,
          true
        );
      }
      return state.nodes;
    });
    const getEdges = computed2(() => {
      if (state.onlyRenderVisibleElements) {
        const visibleEdges = [];
        for (const edge of state.edges) {
          const source = nodeLookup.value.get(edge.source);
          const target = nodeLookup.value.get(edge.target);
          if (isEdgeVisible({
            sourcePos: source.computedPosition || { x: 0, y: 0 },
            targetPos: target.computedPosition || { x: 0, y: 0 },
            sourceWidth: source.dimensions.width,
            sourceHeight: source.dimensions.height,
            targetWidth: target.dimensions.width,
            targetHeight: target.dimensions.height,
            width: state.dimensions.width,
            height: state.dimensions.height,
            viewport: state.viewport
          })) {
            visibleEdges.push(edge);
          }
        }
        return visibleEdges;
      }
      return state.edges;
    });
    const getElements = computed2(() => [...getNodes.value, ...getEdges.value]);
    const getSelectedNodes = computed2(() => {
      const selectedNodes = [];
      for (const node of state.nodes) {
        if (node.selected) {
          selectedNodes.push(node);
        }
      }
      return selectedNodes;
    });
    const getSelectedEdges = computed2(() => {
      const selectedEdges = [];
      for (const edge of state.edges) {
        if (edge.selected) {
          selectedEdges.push(edge);
        }
      }
      return selectedEdges;
    });
    const getSelectedElements = computed2(() => [
      ...getSelectedNodes.value,
      ...getSelectedEdges.value
    ]);
    const getNodesInitialized = computed2(() => {
      const initializedNodes = [];
      for (const node of state.nodes) {
        if (!!node.dimensions.width && !!node.dimensions.height && node.handleBounds !== void 0) {
          initializedNodes.push(node);
        }
      }
      return initializedNodes;
    });
    const areNodesInitialized = computed2(
      () => getNodes.value.length > 0 && getNodesInitialized.value.length === getNodes.value.length
    );
    return {
      getNode,
      getEdge,
      getElements,
      getEdgeTypes,
      getNodeTypes,
      getEdges,
      getNodes,
      getSelectedElements,
      getSelectedNodes,
      getSelectedEdges,
      getNodesInitialized,
      areNodesInitialized
    };
  }
  var Storage = class _Storage {
    constructor() {
      this.currentId = 0;
      this.flows = /* @__PURE__ */ new Map();
    }
    static getInstance() {
      var _a;
      const vueApp = (_a = getCurrentInstance()) == null ? void 0 : _a.appContext.app;
      const existingInstance = (vueApp == null ? void 0 : vueApp.config.globalProperties.$vueFlowStorage) ?? _Storage.instance;
      _Storage.instance = existingInstance ?? new _Storage();
      if (vueApp) {
        vueApp.config.globalProperties.$vueFlowStorage = _Storage.instance;
      }
      return _Storage.instance;
    }
    set(id2, flow) {
      return this.flows.set(id2, flow);
    }
    get(id2) {
      return this.flows.get(id2);
    }
    remove(id2) {
      return this.flows.delete(id2);
    }
    create(id2, preloadedState) {
      const state = useState();
      const reactiveState = reactive(state);
      const hooksOn = {};
      for (const [n, h2] of Object.entries(reactiveState.hooks)) {
        const name = `on${n.charAt(0).toUpperCase() + n.slice(1)}`;
        hooksOn[name] = h2.on;
      }
      const emits = {};
      for (const [n, h2] of Object.entries(reactiveState.hooks)) {
        emits[n] = h2.trigger;
      }
      const nodeLookup = computed2(() => {
        const nodesMap = /* @__PURE__ */ new Map();
        for (const node of reactiveState.nodes) {
          nodesMap.set(node.id, node);
        }
        return nodesMap;
      });
      const edgeLookup = computed2(() => {
        const edgesMap = /* @__PURE__ */ new Map();
        for (const edge of reactiveState.edges) {
          edgesMap.set(edge.id, edge);
        }
        return edgesMap;
      });
      const getters = useGetters(reactiveState, nodeLookup, edgeLookup);
      const actions = useActions(reactiveState, nodeLookup, edgeLookup);
      actions.setState({ ...reactiveState, ...preloadedState });
      const flow = {
        ...hooksOn,
        ...getters,
        ...actions,
        ...toRefs2(reactiveState),
        nodeLookup,
        edgeLookup,
        emits,
        id: id2,
        vueFlowVersion: "1.48.2",
        $destroy: () => {
          this.remove(id2);
        }
      };
      this.set(id2, flow);
      return flow;
    }
    getId() {
      return `vue-flow-${this.currentId++}`;
    }
  };
  function useVueFlow(idOrOpts) {
    const storage = Storage.getInstance();
    const scope = getCurrentScope();
    const isOptsObj = typeof idOrOpts === "object";
    const options = isOptsObj ? idOrOpts : { id: idOrOpts };
    const id2 = options.id;
    const vueFlowId = id2 ?? (scope == null ? void 0 : scope.vueFlowId);
    let vueFlow;
    if (scope) {
      const injectedState = inject(VueFlow, null);
      if (typeof injectedState !== "undefined" && injectedState !== null && (!vueFlowId || injectedState.id === vueFlowId)) {
        vueFlow = injectedState;
      }
    }
    if (!vueFlow) {
      if (vueFlowId) {
        vueFlow = storage.get(vueFlowId);
      }
    }
    if (!vueFlow || vueFlowId && vueFlow.id !== vueFlowId) {
      const name = id2 ?? storage.getId();
      const state = storage.create(name, options);
      vueFlow = state;
      const vfScope = scope ?? effectScope(true);
      vfScope.run(() => {
        watch2(
          state.applyDefault,
          (shouldApplyDefault, __, onCleanup) => {
            const nodesChangeHandler = (changes) => {
              state.applyNodeChanges(changes);
            };
            const edgesChangeHandler = (changes) => {
              state.applyEdgeChanges(changes);
            };
            if (shouldApplyDefault) {
              state.onNodesChange(nodesChangeHandler);
              state.onEdgesChange(edgesChangeHandler);
            } else {
              state.hooks.value.nodesChange.off(nodesChangeHandler);
              state.hooks.value.edgesChange.off(edgesChangeHandler);
            }
            onCleanup(() => {
              state.hooks.value.nodesChange.off(nodesChangeHandler);
              state.hooks.value.edgesChange.off(edgesChangeHandler);
            });
          },
          { immediate: true }
        );
        tryOnScopeDispose(() => {
          if (vueFlow) {
            const storedInstance = storage.get(vueFlow.id);
            if (storedInstance) {
              storedInstance.$destroy();
            } else {
              warn3(`No store instance found for id ${vueFlow.id} in storage.`);
            }
          }
        });
      });
    } else {
      if (isOptsObj) {
        vueFlow.setState(options);
      }
    }
    if (scope) {
      provide(VueFlow, vueFlow);
      scope.vueFlowId = vueFlow.id;
    }
    if (isOptsObj) {
      const instance = getCurrentInstance();
      if ((instance == null ? void 0 : instance.type.name) !== "VueFlow") {
        vueFlow.emits.error(new VueFlowError(ErrorCode.USEVUEFLOW_OPTIONS));
      }
    }
    return vueFlow;
  }
  function useResizeHandler(viewportEl) {
    const { emits, dimensions } = useVueFlow();
    let resizeObserver;
    onMounted(() => {
      const updateDimensions = () => {
        var _a, _b;
        if (!viewportEl.value || !(((_b = (_a = viewportEl.value).checkVisibility) == null ? void 0 : _b.call(_a)) ?? true)) {
          return;
        }
        const size = getDimensions(viewportEl.value);
        if (size.width === 0 || size.height === 0) {
          emits.error(new VueFlowError(ErrorCode.MISSING_VIEWPORT_DIMENSIONS));
        }
        dimensions.value = { width: size.width || 500, height: size.height || 500 };
      };
      updateDimensions();
      window.addEventListener("resize", updateDimensions);
      if (viewportEl.value) {
        resizeObserver = new ResizeObserver(() => updateDimensions());
        resizeObserver.observe(viewportEl.value);
      }
      onBeforeUnmount(() => {
        window.removeEventListener("resize", updateDimensions);
        if (resizeObserver && viewportEl.value) {
          resizeObserver.unobserve(viewportEl.value);
        }
      });
    });
  }
  var __default__$c = {
    name: "UserSelection",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$c = /* @__PURE__ */ defineComponent({
    ...__default__$c,
    props: {
      userSelectionRect: {}
    },
    setup(__props) {
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          class: "vue-flow__selection vue-flow__container",
          style: normalizeStyle({
            width: `${_ctx.userSelectionRect.width}px`,
            height: `${_ctx.userSelectionRect.height}px`,
            transform: `translate(${_ctx.userSelectionRect.x}px, ${_ctx.userSelectionRect.y}px)`
          })
        }, null, 4);
      };
    }
  });
  var _hoisted_1$6 = ["tabIndex"];
  var __default__$b = {
    name: "NodesSelection",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$b = /* @__PURE__ */ defineComponent({
    ...__default__$b,
    setup(__props) {
      const { emits, viewport, getSelectedNodes, noPanClassName, disableKeyboardA11y, userSelectionActive } = useVueFlow();
      const updatePositions = useUpdateNodePositions();
      const el = ref(null);
      const dragging = useDrag({
        el,
        onStart(args) {
          emits.selectionDragStart(args);
          emits.nodeDragStart(args);
        },
        onDrag(args) {
          emits.selectionDrag(args);
          emits.nodeDrag(args);
        },
        onStop(args) {
          emits.selectionDragStop(args);
          emits.nodeDragStop(args);
        }
      });
      onMounted(() => {
        var _a;
        if (!disableKeyboardA11y.value) {
          (_a = el.value) == null ? void 0 : _a.focus({ preventScroll: true });
        }
      });
      const selectedNodesBBox = computed2(() => getRectOfNodes(getSelectedNodes.value));
      const innerStyle = computed2(() => ({
        width: `${selectedNodesBBox.value.width}px`,
        height: `${selectedNodesBBox.value.height}px`,
        top: `${selectedNodesBBox.value.y}px`,
        left: `${selectedNodesBBox.value.x}px`
      }));
      function onContextMenu(event) {
        emits.selectionContextMenu({ event, nodes: getSelectedNodes.value });
      }
      function onKeyDown(event) {
        if (disableKeyboardA11y.value) {
          return;
        }
        if (arrowKeyDiffs[event.key]) {
          event.preventDefault();
          updatePositions(
            {
              x: arrowKeyDiffs[event.key].x,
              y: arrowKeyDiffs[event.key].y
            },
            event.shiftKey
          );
        }
      }
      return (_ctx, _cache) => {
        return !unref(userSelectionActive) && selectedNodesBBox.value.width && selectedNodesBBox.value.height ? (openBlock(), createElementBlock("div", {
          key: 0,
          class: normalizeClass(["vue-flow__nodesselection vue-flow__container", unref(noPanClassName)]),
          style: normalizeStyle({ transform: `translate(${unref(viewport).x}px,${unref(viewport).y}px) scale(${unref(viewport).zoom})` })
        }, [
          createBaseVNode("div", {
            ref_key: "el",
            ref: el,
            class: normalizeClass([{ dragging: unref(dragging) }, "vue-flow__nodesselection-rect"]),
            style: normalizeStyle(innerStyle.value),
            tabIndex: unref(disableKeyboardA11y) ? void 0 : -1,
            onContextmenu: onContextMenu,
            onKeydown: onKeyDown
          }, null, 46, _hoisted_1$6)
        ], 6)) : createCommentVNode("", true);
      };
    }
  });
  function getMousePosition(event, containerBounds) {
    return {
      x: event.clientX - containerBounds.left,
      y: event.clientY - containerBounds.top
    };
  }
  var __default__$a = {
    name: "Pane",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$a = /* @__PURE__ */ defineComponent({
    ...__default__$a,
    props: {
      isSelecting: { type: Boolean },
      selectionKeyPressed: { type: Boolean }
    },
    setup(__props) {
      const {
        vueFlowRef,
        nodes,
        viewport,
        emits,
        userSelectionActive,
        removeSelectedElements,
        userSelectionRect,
        elementsSelectable,
        nodesSelectionActive,
        getSelectedEdges,
        getSelectedNodes,
        removeNodes,
        removeEdges,
        selectionMode,
        deleteKeyCode,
        multiSelectionKeyCode,
        multiSelectionActive,
        edgeLookup,
        nodeLookup,
        connectionLookup,
        defaultEdgeOptions,
        connectionStartHandle,
        panOnDrag
      } = useVueFlow();
      const container = shallowRef(null);
      const selectedNodeIds = shallowRef(/* @__PURE__ */ new Set());
      const selectedEdgeIds = shallowRef(/* @__PURE__ */ new Set());
      const containerBounds = shallowRef(null);
      const hasActiveSelection = toRef(() => elementsSelectable.value && (__props.isSelecting || userSelectionActive.value));
      const connectionInProgress = toRef(() => connectionStartHandle.value !== null);
      let selectionInProgress = false;
      let selectionStarted = false;
      const deleteKeyPressed = useKeyPress(deleteKeyCode, { actInsideInputWithModifier: false });
      const multiSelectKeyPressed = useKeyPress(multiSelectionKeyCode);
      watch2(deleteKeyPressed, (isKeyPressed) => {
        if (!isKeyPressed) {
          return;
        }
        removeNodes(getSelectedNodes.value);
        removeEdges(getSelectedEdges.value);
        nodesSelectionActive.value = false;
      });
      watch2(multiSelectKeyPressed, (isKeyPressed) => {
        multiSelectionActive.value = isKeyPressed;
      });
      function wrapHandler(handler, containerRef) {
        return (event) => {
          if (event.target !== containerRef) {
            return;
          }
          handler == null ? void 0 : handler(event);
        };
      }
      function onClick(event) {
        if (selectionInProgress || connectionInProgress.value) {
          selectionInProgress = false;
          return;
        }
        emits.paneClick(event);
        removeSelectedElements();
        nodesSelectionActive.value = false;
      }
      function onContextMenu(event) {
        var _a;
        if (Array.isArray(panOnDrag.value) && ((_a = panOnDrag.value) == null ? void 0 : _a.includes(2))) {
          event.preventDefault();
          return;
        }
        emits.paneContextMenu(event);
      }
      function onWheel(event) {
        emits.paneScroll(event);
      }
      function onPointerDown(event) {
        var _a, _b, _c;
        containerBounds.value = ((_a = vueFlowRef.value) == null ? void 0 : _a.getBoundingClientRect()) ?? null;
        if (!elementsSelectable.value || !__props.isSelecting || event.button !== 0 || event.target !== container.value || !containerBounds.value) {
          return;
        }
        (_c = (_b = event.target) == null ? void 0 : _b.setPointerCapture) == null ? void 0 : _c.call(_b, event.pointerId);
        const { x, y } = getMousePosition(event, containerBounds.value);
        selectionStarted = true;
        selectionInProgress = false;
        removeSelectedElements();
        userSelectionRect.value = {
          width: 0,
          height: 0,
          startX: x,
          startY: y,
          x,
          y
        };
        emits.selectionStart(event);
      }
      function onPointerMove(event) {
        var _a;
        if (!containerBounds.value || !userSelectionRect.value) {
          return;
        }
        selectionInProgress = true;
        const { x: mouseX, y: mouseY } = getEventPosition(event, containerBounds.value);
        const { startX = 0, startY = 0 } = userSelectionRect.value;
        const nextUserSelectRect = {
          startX,
          startY,
          x: mouseX < startX ? mouseX : startX,
          y: mouseY < startY ? mouseY : startY,
          width: Math.abs(mouseX - startX),
          height: Math.abs(mouseY - startY)
        };
        const prevSelectedNodeIds = selectedNodeIds.value;
        const prevSelectedEdgeIds = selectedEdgeIds.value;
        selectedNodeIds.value = new Set(
          getNodesInside(nodes.value, nextUserSelectRect, viewport.value, selectionMode.value === SelectionMode.Partial, true).map(
            (node) => node.id
          )
        );
        selectedEdgeIds.value = /* @__PURE__ */ new Set();
        const edgesSelectable = ((_a = defaultEdgeOptions.value) == null ? void 0 : _a.selectable) ?? true;
        for (const nodeId of selectedNodeIds.value) {
          const connections = connectionLookup.value.get(nodeId);
          if (!connections) {
            continue;
          }
          for (const { edgeId } of connections.values()) {
            const edge = edgeLookup.value.get(edgeId);
            if (edge && (edge.selectable ?? edgesSelectable)) {
              selectedEdgeIds.value.add(edgeId);
            }
          }
        }
        if (!areSetsEqual(prevSelectedNodeIds, selectedNodeIds.value)) {
          const changes = getSelectionChanges(nodeLookup.value, selectedNodeIds.value, true);
          emits.nodesChange(changes);
        }
        if (!areSetsEqual(prevSelectedEdgeIds, selectedEdgeIds.value)) {
          const changes = getSelectionChanges(edgeLookup.value, selectedEdgeIds.value);
          emits.edgesChange(changes);
        }
        userSelectionRect.value = nextUserSelectRect;
        userSelectionActive.value = true;
        nodesSelectionActive.value = false;
      }
      function onPointerUp(event) {
        var _a;
        if (event.button !== 0 || !selectionStarted) {
          return;
        }
        (_a = event.target) == null ? void 0 : _a.releasePointerCapture(event.pointerId);
        if (!userSelectionActive.value && userSelectionRect.value && event.target === container.value) {
          onClick(event);
        }
        userSelectionActive.value = false;
        userSelectionRect.value = null;
        nodesSelectionActive.value = selectedNodeIds.value.size > 0;
        emits.selectionEnd(event);
        if (__props.selectionKeyPressed) {
          selectionInProgress = false;
        }
        selectionStarted = false;
      }
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          ref_key: "container",
          ref: container,
          class: normalizeClass(["vue-flow__pane vue-flow__container", { selection: _ctx.isSelecting }]),
          onClick: _cache[0] || (_cache[0] = (event) => hasActiveSelection.value ? void 0 : wrapHandler(onClick, container.value)(event)),
          onContextmenu: _cache[1] || (_cache[1] = ($event) => wrapHandler(onContextMenu, container.value)($event)),
          onWheelPassive: _cache[2] || (_cache[2] = ($event) => wrapHandler(onWheel, container.value)($event)),
          onPointerenter: _cache[3] || (_cache[3] = (event) => hasActiveSelection.value ? void 0 : unref(emits).paneMouseEnter(event)),
          onPointerdown: _cache[4] || (_cache[4] = (event) => hasActiveSelection.value ? onPointerDown(event) : unref(emits).paneMouseMove(event)),
          onPointermove: _cache[5] || (_cache[5] = (event) => hasActiveSelection.value ? onPointerMove(event) : unref(emits).paneMouseMove(event)),
          onPointerup: _cache[6] || (_cache[6] = (event) => hasActiveSelection.value ? onPointerUp(event) : void 0),
          onPointerleave: _cache[7] || (_cache[7] = ($event) => unref(emits).paneMouseLeave($event))
        }, [
          renderSlot(_ctx.$slots, "default"),
          unref(userSelectionActive) && unref(userSelectionRect) ? (openBlock(), createBlock(_sfc_main$c, {
            key: 0,
            "user-selection-rect": unref(userSelectionRect)
          }, null, 8, ["user-selection-rect"])) : createCommentVNode("", true),
          unref(nodesSelectionActive) && unref(getSelectedNodes).length ? (openBlock(), createBlock(_sfc_main$b, { key: 1 })) : createCommentVNode("", true)
        ], 34);
      };
    }
  });
  var __default__$9 = {
    name: "Transform",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$9 = /* @__PURE__ */ defineComponent({
    ...__default__$9,
    setup(__props) {
      const { viewport, fitViewOnInit, fitViewOnInitDone } = useVueFlow();
      const isHidden = computed2(() => {
        if (fitViewOnInit.value) {
          return !fitViewOnInitDone.value;
        }
        return false;
      });
      const transform = computed2(() => `translate(${viewport.value.x}px,${viewport.value.y}px) scale(${viewport.value.zoom})`);
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          class: "vue-flow__transformationpane vue-flow__container",
          style: normalizeStyle({ transform: transform.value, opacity: isHidden.value ? 0 : void 0 })
        }, [
          renderSlot(_ctx.$slots, "default")
        ], 4);
      };
    }
  });
  var __default__$8 = {
    name: "Viewport",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$8 = /* @__PURE__ */ defineComponent({
    ...__default__$8,
    setup(__props) {
      const {
        minZoom,
        maxZoom,
        defaultViewport,
        translateExtent,
        zoomActivationKeyCode,
        selectionKeyCode,
        panActivationKeyCode,
        panOnScroll,
        panOnScrollMode,
        panOnScrollSpeed,
        panOnDrag,
        zoomOnDoubleClick,
        zoomOnPinch,
        zoomOnScroll,
        preventScrolling,
        noWheelClassName,
        noPanClassName,
        emits,
        connectionStartHandle,
        userSelectionActive,
        paneDragging,
        d3Zoom: storeD3Zoom,
        d3Selection: storeD3Selection,
        d3ZoomHandler: storeD3ZoomHandler,
        viewport,
        viewportRef,
        paneClickDistance
      } = useVueFlow();
      useResizeHandler(viewportRef);
      const isZoomingOrPanning = shallowRef(false);
      const isPanScrolling = shallowRef(false);
      let panScrollTimeout = null;
      let zoomedWithRightMouseButton = false;
      let mouseButton = 0;
      let prevTransform = {
        x: 0,
        y: 0,
        zoom: 0
      };
      const panKeyPressed = useKeyPress(panActivationKeyCode);
      const selectionKeyPressed = useKeyPress(selectionKeyCode);
      const zoomKeyPressed = useKeyPress(zoomActivationKeyCode);
      const shouldPanOnDrag = toRef(
        () => (!selectionKeyPressed.value || selectionKeyPressed.value && selectionKeyCode.value === true) && (panKeyPressed.value || panOnDrag.value)
      );
      const shouldPanOnScroll = toRef(() => panKeyPressed.value || panOnScroll.value);
      const shouldSelectOnDrag = toRef(() => selectionKeyCode.value === true && shouldPanOnDrag.value !== true);
      const isSelecting = toRef(
        () => selectionKeyPressed.value && selectionKeyCode.value !== true || userSelectionActive.value || shouldSelectOnDrag.value
      );
      const connectionInProgress = toRef(() => connectionStartHandle.value !== null);
      onMounted(() => {
        if (!viewportRef.value) {
          warn3("Viewport element is missing");
          return;
        }
        const viewportElement = viewportRef.value;
        const bbox = viewportElement.getBoundingClientRect();
        const d3Zoom = zoom().clickDistance(paneClickDistance.value).scaleExtent([minZoom.value, maxZoom.value]).translateExtent(translateExtent.value);
        const d3Selection = select(viewportElement).call(d3Zoom);
        const d3ZoomHandler = d3Selection.on("wheel.zoom");
        const updatedTransform = identity.translate(defaultViewport.value.x ?? 0, defaultViewport.value.y ?? 0).scale(clamp(defaultViewport.value.zoom ?? 1, minZoom.value, maxZoom.value));
        const extent = [
          [0, 0],
          [bbox.width, bbox.height]
        ];
        const constrainedTransform = d3Zoom.constrain()(updatedTransform, extent, translateExtent.value);
        d3Zoom.transform(d3Selection, constrainedTransform);
        d3Zoom.wheelDelta(wheelDelta);
        storeD3Zoom.value = d3Zoom;
        storeD3Selection.value = d3Selection;
        storeD3ZoomHandler.value = d3ZoomHandler;
        viewport.value = { x: constrainedTransform.x, y: constrainedTransform.y, zoom: constrainedTransform.k };
        d3Zoom.on("start", (event) => {
          var _a;
          if (!event.sourceEvent) {
            return null;
          }
          mouseButton = event.sourceEvent.button;
          isZoomingOrPanning.value = true;
          const flowTransform = eventToFlowTransform(event.transform);
          if (((_a = event.sourceEvent) == null ? void 0 : _a.type) === "mousedown") {
            paneDragging.value = true;
          }
          prevTransform = flowTransform;
          emits.viewportChangeStart(flowTransform);
          emits.moveStart({ event, flowTransform });
        });
        d3Zoom.on("end", (event) => {
          if (!event.sourceEvent) {
            return null;
          }
          isZoomingOrPanning.value = false;
          paneDragging.value = false;
          if (isRightClickPan(shouldPanOnDrag.value, mouseButton ?? 0) && !zoomedWithRightMouseButton) {
            emits.paneContextMenu(event.sourceEvent);
          }
          zoomedWithRightMouseButton = false;
          if (viewChanged(prevTransform, event.transform)) {
            const flowTransform = eventToFlowTransform(event.transform);
            prevTransform = flowTransform;
            emits.viewportChangeEnd(flowTransform);
            emits.moveEnd({ event, flowTransform });
          }
        });
        d3Zoom.filter((event) => {
          var _a;
          const zoomScroll = zoomKeyPressed.value || zoomOnScroll.value;
          const pinchZoom = zoomOnPinch.value && event.ctrlKey;
          const eventButton = event.button;
          const isWheelEvent = event.type === "wheel";
          if (eventButton === 1 && event.type === "mousedown" && (isWrappedWithClass(event, "vue-flow__node") || isWrappedWithClass(event, "vue-flow__edge"))) {
            return true;
          }
          if (!shouldPanOnDrag.value && !zoomScroll && !shouldPanOnScroll.value && !zoomOnDoubleClick.value && !zoomOnPinch.value) {
            return false;
          }
          if (userSelectionActive.value) {
            return false;
          }
          if (connectionInProgress.value && !isWheelEvent) {
            return false;
          }
          if (!zoomOnDoubleClick.value && event.type === "dblclick") {
            return false;
          }
          if (isWrappedWithClass(event, noWheelClassName.value) && isWheelEvent) {
            return false;
          }
          if (isWrappedWithClass(event, noPanClassName.value) && (!isWheelEvent || shouldPanOnScroll.value && isWheelEvent && !zoomKeyPressed.value)) {
            return false;
          }
          if (!zoomOnPinch.value && event.ctrlKey && isWheelEvent) {
            return false;
          }
          if (!zoomScroll && !shouldPanOnScroll.value && !pinchZoom && isWheelEvent) {
            return false;
          }
          if (!zoomOnPinch && event.type === "touchstart" && ((_a = event.touches) == null ? void 0 : _a.length) > 1) {
            event.preventDefault();
            return false;
          }
          if (!shouldPanOnDrag.value && (event.type === "mousedown" || event.type === "touchstart")) {
            return false;
          }
          if (shouldSelectOnDrag.value && Array.isArray(panOnDrag.value) && panOnDrag.value.includes(0) && eventButton === 0) {
            return false;
          }
          if (Array.isArray(panOnDrag.value) && !panOnDrag.value.includes(eventButton) && (event.type === "mousedown" || event.type === "touchstart")) {
            return false;
          }
          const buttonAllowed = Array.isArray(panOnDrag.value) && panOnDrag.value.includes(eventButton) || selectionKeyCode.value === true && Array.isArray(panOnDrag.value) && !panOnDrag.value.includes(0) || !eventButton || eventButton <= 1;
          return (!event.ctrlKey || panKeyPressed.value || isWheelEvent) && buttonAllowed;
        });
        watch2(
          [userSelectionActive, shouldPanOnDrag],
          () => {
            if (userSelectionActive.value && !isZoomingOrPanning.value) {
              d3Zoom.on("zoom", null);
            } else if (!userSelectionActive.value) {
              d3Zoom.on("zoom", (event) => {
                viewport.value = { x: event.transform.x, y: event.transform.y, zoom: event.transform.k };
                const flowTransform = eventToFlowTransform(event.transform);
                zoomedWithRightMouseButton = isRightClickPan(shouldPanOnDrag.value, mouseButton ?? 0);
                emits.viewportChange(flowTransform);
                emits.move({ event, flowTransform });
              });
            }
          },
          { immediate: true }
        );
        watch2(
          [userSelectionActive, shouldPanOnScroll, panOnScrollMode, zoomKeyPressed, zoomOnPinch, preventScrolling, noWheelClassName],
          () => {
            if (shouldPanOnScroll.value && !zoomKeyPressed.value && !userSelectionActive.value) {
              d3Selection.on(
                "wheel.zoom",
                (event) => {
                  if (isWrappedWithClass(event, noWheelClassName.value)) {
                    return false;
                  }
                  const zoomScroll = zoomKeyPressed.value || zoomOnScroll.value;
                  const pinchZoom = zoomOnPinch.value && event.ctrlKey;
                  const scrollEventEnabled = !preventScrolling.value || shouldPanOnScroll.value || zoomScroll || pinchZoom;
                  if (!scrollEventEnabled) {
                    return false;
                  }
                  event.preventDefault();
                  event.stopImmediatePropagation();
                  const currentZoom = d3Selection.property("__zoom").k || 1;
                  const _isMacOs = isMacOs();
                  if (!panKeyPressed.value && event.ctrlKey && zoomOnPinch.value && _isMacOs) {
                    const point = pointer(event);
                    const pinchDelta = wheelDelta(event);
                    const zoom2 = currentZoom * 2 ** pinchDelta;
                    d3Zoom.scaleTo(d3Selection, zoom2, point, event);
                    return;
                  }
                  const deltaNormalize = event.deltaMode === 1 ? 20 : 1;
                  let deltaX = panOnScrollMode.value === PanOnScrollMode.Vertical ? 0 : event.deltaX * deltaNormalize;
                  let deltaY = panOnScrollMode.value === PanOnScrollMode.Horizontal ? 0 : event.deltaY * deltaNormalize;
                  if (!_isMacOs && event.shiftKey && panOnScrollMode.value !== PanOnScrollMode.Vertical && !deltaX && deltaY) {
                    deltaX = deltaY;
                    deltaY = 0;
                  }
                  d3Zoom.translateBy(
                    d3Selection,
                    -(deltaX / currentZoom) * panOnScrollSpeed.value,
                    -(deltaY / currentZoom) * panOnScrollSpeed.value
                  );
                  const nextViewport = eventToFlowTransform(d3Selection.property("__zoom"));
                  if (panScrollTimeout) {
                    clearTimeout(panScrollTimeout);
                  }
                  if (!isPanScrolling.value) {
                    isPanScrolling.value = true;
                    emits.moveStart({ event, flowTransform: nextViewport });
                    emits.viewportChangeStart(nextViewport);
                  } else {
                    emits.move({ event, flowTransform: nextViewport });
                    emits.viewportChange(nextViewport);
                    panScrollTimeout = setTimeout(() => {
                      emits.moveEnd({ event, flowTransform: nextViewport });
                      emits.viewportChangeEnd(nextViewport);
                      isPanScrolling.value = false;
                    }, 150);
                  }
                },
                { passive: false }
              );
            } else if (typeof d3ZoomHandler !== "undefined") {
              d3Selection.on(
                "wheel.zoom",
                function(event, d) {
                  const invalidEvent = !preventScrolling.value && event.type === "wheel" && !event.ctrlKey;
                  const zoomScroll = zoomKeyPressed.value || zoomOnScroll.value;
                  const pinchZoom = zoomOnPinch.value && event.ctrlKey;
                  const scrollEventsDisabled = !zoomScroll && !panOnScroll.value && !pinchZoom && event.type === "wheel";
                  if (scrollEventsDisabled || invalidEvent || isWrappedWithClass(event, noWheelClassName.value)) {
                    return null;
                  }
                  event.preventDefault();
                  d3ZoomHandler.call(this, event, d);
                },
                { passive: false }
              );
            }
          },
          { immediate: true }
        );
      });
      function isRightClickPan(pan, usedButton) {
        return usedButton === 2 && Array.isArray(pan) && pan.includes(2);
      }
      function viewChanged(prevViewport, eventTransform) {
        return prevViewport.x !== eventTransform.x && !Number.isNaN(eventTransform.x) || prevViewport.y !== eventTransform.y && !Number.isNaN(eventTransform.y) || prevViewport.zoom !== eventTransform.k && !Number.isNaN(eventTransform.k);
      }
      function eventToFlowTransform(eventTransform) {
        return {
          x: eventTransform.x,
          y: eventTransform.y,
          zoom: eventTransform.k
        };
      }
      function isWrappedWithClass(event, className) {
        return event.target.closest(`.${className}`);
      }
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          ref_key: "viewportRef",
          ref: viewportRef,
          class: "vue-flow__viewport vue-flow__container"
        }, [
          createVNode(_sfc_main$a, {
            "is-selecting": isSelecting.value,
            "selection-key-pressed": unref(selectionKeyPressed),
            class: normalizeClass({
              connecting: connectionInProgress.value,
              dragging: unref(paneDragging),
              draggable: unref(panOnDrag) === true || Array.isArray(unref(panOnDrag)) && unref(panOnDrag).includes(0)
            })
          }, {
            default: withCtx(() => [
              createVNode(_sfc_main$9, null, {
                default: withCtx(() => [
                  renderSlot(_ctx.$slots, "default")
                ]),
                _: 3
              })
            ]),
            _: 3
          }, 8, ["is-selecting", "selection-key-pressed", "class"])
        ], 512);
      };
    }
  });
  var _hoisted_1$5 = ["id"];
  var _hoisted_2 = ["id"];
  var _hoisted_3 = ["id"];
  var __default__$7 = {
    name: "A11yDescriptions",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$7 = /* @__PURE__ */ defineComponent({
    ...__default__$7,
    setup(__props) {
      const { id: id2, disableKeyboardA11y, ariaLiveMessage } = useVueFlow();
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock(Fragment, null, [
          createBaseVNode("div", {
            id: `${unref(ARIA_NODE_DESC_KEY)}-${unref(id2)}`,
            style: { "display": "none" }
          }, " Press enter or space to select a node. " + toDisplayString(!unref(disableKeyboardA11y) ? "You can then use the arrow keys to move the node around." : "") + " You can then use the arrow keys to move the node around, press delete to remove it and press escape to cancel. ", 9, _hoisted_1$5),
          createBaseVNode("div", {
            id: `${unref(ARIA_EDGE_DESC_KEY)}-${unref(id2)}`,
            style: { "display": "none" }
          }, " Press enter or space to select an edge. You can then press delete to remove it or press escape to cancel. ", 8, _hoisted_2),
          !unref(disableKeyboardA11y) ? (openBlock(), createElementBlock("div", {
            key: 0,
            id: `${unref(ARIA_LIVE_MESSAGE)}-${unref(id2)}`,
            "aria-live": "assertive",
            "aria-atomic": "true",
            style: { "position": "absolute", "width": "1px", "height": "1px", "margin": "-1px", "border": "0", "padding": "0", "overflow": "hidden", "clip": "rect(0px, 0px, 0px, 0px)", "clip-path": "inset(100%)" }
          }, toDisplayString(unref(ariaLiveMessage)), 9, _hoisted_3)) : createCommentVNode("", true)
        ], 64);
      };
    }
  });
  function useOnInitHandler() {
    const vfInstance = useVueFlow();
    watch2(
      () => vfInstance.viewportHelper.value.viewportInitialized,
      (isInitialized) => {
        if (isInitialized) {
          setTimeout(() => {
            vfInstance.emits.init(vfInstance);
            vfInstance.emits.paneReady(vfInstance);
          }, 1);
        }
      }
    );
  }
  function shiftX(x, shift, position) {
    if (position === Position.Left) {
      return x - shift;
    }
    if (position === Position.Right) {
      return x + shift;
    }
    return x;
  }
  function shiftY(y, shift, position) {
    if (position === Position.Top) {
      return y - shift;
    }
    if (position === Position.Bottom) {
      return y + shift;
    }
    return y;
  }
  var EdgeAnchor = function({
    radius = 10,
    centerX = 0,
    centerY = 0,
    position = Position.Top,
    type
  }) {
    return h("circle", {
      class: `vue-flow__edgeupdater vue-flow__edgeupdater-${type}`,
      cx: shiftX(centerX, radius, position),
      cy: shiftY(centerY, radius, position),
      r: radius,
      stroke: "transparent",
      fill: "transparent"
    });
  };
  EdgeAnchor.props = ["radius", "centerX", "centerY", "position", "type"];
  EdgeAnchor.compatConfig = { MODE: 3 };
  var EdgeAnchor$1 = EdgeAnchor;
  var EdgeWrapper = defineComponent({
    name: "Edge",
    compatConfig: { MODE: 3 },
    props: ["id"],
    setup(props) {
      const {
        id: vueFlowId,
        addSelectedEdges,
        connectionMode,
        edgeUpdaterRadius,
        emits,
        nodesSelectionActive,
        noPanClassName,
        getEdgeTypes,
        removeSelectedEdges,
        findEdge,
        findNode,
        isValidConnection,
        multiSelectionActive,
        disableKeyboardA11y,
        elementsSelectable,
        edgesUpdatable,
        edgesFocusable,
        hooks
      } = useVueFlow();
      const edge = computed2(() => findEdge(props.id));
      const { emit: emit2, on } = useEdgeHooks(edge.value, emits);
      const slots = inject(Slots);
      const instance = getCurrentInstance();
      const mouseOver = ref(false);
      const updating = ref(false);
      const nodeId = ref("");
      const handleId = ref(null);
      const edgeUpdaterType = ref("source");
      const edgeEl = ref(null);
      const isSelectable = toRef(
        () => typeof edge.value.selectable === "undefined" ? elementsSelectable.value : edge.value.selectable
      );
      const isUpdatable = toRef(() => typeof edge.value.updatable === "undefined" ? edgesUpdatable.value : edge.value.updatable);
      const isFocusable = toRef(() => typeof edge.value.focusable === "undefined" ? edgesFocusable.value : edge.value.focusable);
      provide(EdgeId, props.id);
      provide(EdgeRef, edgeEl);
      const edgeClass = computed2(() => edge.value.class instanceof Function ? edge.value.class(edge.value) : edge.value.class);
      const edgeStyle = computed2(() => edge.value.style instanceof Function ? edge.value.style(edge.value) : edge.value.style);
      const edgeCmp = computed2(() => {
        const name = edge.value.type || "default";
        const slot = slots == null ? void 0 : slots[`edge-${name}`];
        if (slot) {
          return slot;
        }
        let edgeType = edge.value.template ?? getEdgeTypes.value[name];
        if (typeof edgeType === "string") {
          if (instance) {
            const components = Object.keys(instance.appContext.components);
            if (components && components.includes(name)) {
              edgeType = resolveComponent(name, false);
            }
          }
        }
        if (edgeType && typeof edgeType !== "string") {
          return edgeType;
        }
        emits.error(new VueFlowError(ErrorCode.EDGE_TYPE_MISSING, edgeType));
        return false;
      });
      const { handlePointerDown } = useHandle({
        nodeId,
        handleId,
        type: edgeUpdaterType,
        isValidConnection,
        edgeUpdaterType,
        onEdgeUpdate,
        onEdgeUpdateEnd
      });
      return () => {
        const sourceNode = findNode(edge.value.source);
        const targetNode = findNode(edge.value.target);
        const pathOptions = "pathOptions" in edge.value ? edge.value.pathOptions : {};
        if (!sourceNode && !targetNode) {
          emits.error(new VueFlowError(ErrorCode.EDGE_SOURCE_TARGET_MISSING, edge.value.id, edge.value.source, edge.value.target));
          return null;
        }
        if (!sourceNode) {
          emits.error(new VueFlowError(ErrorCode.EDGE_SOURCE_MISSING, edge.value.id, edge.value.source));
          return null;
        }
        if (!targetNode) {
          emits.error(new VueFlowError(ErrorCode.EDGE_TARGET_MISSING, edge.value.id, edge.value.target));
          return null;
        }
        if (!edge.value || edge.value.hidden || sourceNode.hidden || targetNode.hidden) {
          return null;
        }
        let sourceNodeHandles;
        if (connectionMode.value === ConnectionMode.Strict) {
          sourceNodeHandles = sourceNode.handleBounds.source;
        } else {
          sourceNodeHandles = [...sourceNode.handleBounds.source || [], ...sourceNode.handleBounds.target || []];
        }
        const sourceHandle = getEdgeHandle(sourceNodeHandles, edge.value.sourceHandle);
        let targetNodeHandles;
        if (connectionMode.value === ConnectionMode.Strict) {
          targetNodeHandles = targetNode.handleBounds.target;
        } else {
          targetNodeHandles = [...targetNode.handleBounds.target || [], ...targetNode.handleBounds.source || []];
        }
        const targetHandle = getEdgeHandle(targetNodeHandles, edge.value.targetHandle);
        const sourcePosition = (sourceHandle == null ? void 0 : sourceHandle.position) || Position.Bottom;
        const targetPosition = (targetHandle == null ? void 0 : targetHandle.position) || Position.Top;
        const { x: sourceX, y: sourceY } = getHandlePosition(sourceNode, sourceHandle, sourcePosition);
        const { x: targetX, y: targetY } = getHandlePosition(targetNode, targetHandle, targetPosition);
        edge.value.sourceX = sourceX;
        edge.value.sourceY = sourceY;
        edge.value.targetX = targetX;
        edge.value.targetY = targetY;
        return h(
          "g",
          {
            "ref": edgeEl,
            "key": props.id,
            "data-id": props.id,
            "class": [
              "vue-flow__edge",
              `vue-flow__edge-${edgeCmp.value === false ? "default" : edge.value.type || "default"}`,
              noPanClassName.value,
              edgeClass.value,
              {
                updating: mouseOver.value,
                selected: edge.value.selected,
                animated: edge.value.animated,
                inactive: !isSelectable.value && !hooks.value.edgeClick.hasListeners()
              }
            ],
            "tabIndex": isFocusable.value ? 0 : void 0,
            "aria-label": edge.value.ariaLabel === null ? void 0 : edge.value.ariaLabel ?? `Edge from ${edge.value.source} to ${edge.value.target}`,
            "aria-describedby": isFocusable.value ? `${ARIA_EDGE_DESC_KEY}-${vueFlowId}` : void 0,
            "aria-roledescription": "edge",
            "role": isFocusable.value ? "group" : "img",
            ...edge.value.domAttributes,
            "onClick": onEdgeClick,
            "onContextmenu": onEdgeContextMenu,
            "onDblclick": onDoubleClick,
            "onMouseenter": onEdgeMouseEnter,
            "onMousemove": onEdgeMouseMove,
            "onMouseleave": onEdgeMouseLeave,
            "onKeyDown": isFocusable.value ? onKeyDown : void 0
          },
          [
            updating.value ? null : h(edgeCmp.value === false ? getEdgeTypes.value.default : edgeCmp.value, {
              id: props.id,
              sourceNode,
              targetNode,
              source: edge.value.source,
              target: edge.value.target,
              type: edge.value.type,
              updatable: isUpdatable.value,
              selected: edge.value.selected,
              animated: edge.value.animated,
              label: edge.value.label,
              labelStyle: edge.value.labelStyle,
              labelShowBg: edge.value.labelShowBg,
              labelBgStyle: edge.value.labelBgStyle,
              labelBgPadding: edge.value.labelBgPadding,
              labelBgBorderRadius: edge.value.labelBgBorderRadius,
              data: edge.value.data,
              events: { ...edge.value.events, ...on },
              style: edgeStyle.value,
              markerStart: `url('#${getMarkerId(edge.value.markerStart, vueFlowId)}')`,
              markerEnd: `url('#${getMarkerId(edge.value.markerEnd, vueFlowId)}')`,
              sourcePosition,
              targetPosition,
              sourceX,
              sourceY,
              targetX,
              targetY,
              sourceHandleId: edge.value.sourceHandle,
              targetHandleId: edge.value.targetHandle,
              interactionWidth: edge.value.interactionWidth,
              ...pathOptions
            }),
            [
              isUpdatable.value === "source" || isUpdatable.value === true ? [
                h(
                  "g",
                  {
                    onMousedown: onEdgeUpdaterSourceMouseDown,
                    onMouseenter: onEdgeUpdaterMouseEnter,
                    onMouseout: onEdgeUpdaterMouseOut
                  },
                  h(EdgeAnchor$1, {
                    "position": sourcePosition,
                    "centerX": sourceX,
                    "centerY": sourceY,
                    "radius": edgeUpdaterRadius.value,
                    "type": "source",
                    "data-type": "source"
                  })
                )
              ] : null,
              isUpdatable.value === "target" || isUpdatable.value === true ? [
                h(
                  "g",
                  {
                    onMousedown: onEdgeUpdaterTargetMouseDown,
                    onMouseenter: onEdgeUpdaterMouseEnter,
                    onMouseout: onEdgeUpdaterMouseOut
                  },
                  h(EdgeAnchor$1, {
                    "position": targetPosition,
                    "centerX": targetX,
                    "centerY": targetY,
                    "radius": edgeUpdaterRadius.value,
                    "type": "target",
                    "data-type": "target"
                  })
                )
              ] : null
            ]
          ]
        );
      };
      function onEdgeUpdaterMouseEnter() {
        mouseOver.value = true;
      }
      function onEdgeUpdaterMouseOut() {
        mouseOver.value = false;
      }
      function onEdgeUpdate(event, connection) {
        emit2.update({ event, edge: edge.value, connection });
      }
      function onEdgeUpdateEnd(event) {
        emit2.updateEnd({ event, edge: edge.value });
        updating.value = false;
      }
      function handleEdgeUpdater(event, isSourceHandle) {
        if (event.button !== 0) {
          return;
        }
        updating.value = true;
        nodeId.value = isSourceHandle ? edge.value.target : edge.value.source;
        handleId.value = (isSourceHandle ? edge.value.targetHandle : edge.value.sourceHandle) ?? null;
        edgeUpdaterType.value = isSourceHandle ? "target" : "source";
        emit2.updateStart({ event, edge: edge.value });
        handlePointerDown(event);
      }
      function onEdgeClick(event) {
        var _a;
        const data = { event, edge: edge.value };
        if (isSelectable.value) {
          nodesSelectionActive.value = false;
          if (edge.value.selected && multiSelectionActive.value) {
            removeSelectedEdges([edge.value]);
            (_a = edgeEl.value) == null ? void 0 : _a.blur();
          } else {
            addSelectedEdges([edge.value]);
          }
        }
        emit2.click(data);
      }
      function onEdgeContextMenu(event) {
        emit2.contextMenu({ event, edge: edge.value });
      }
      function onDoubleClick(event) {
        emit2.doubleClick({ event, edge: edge.value });
      }
      function onEdgeMouseEnter(event) {
        emit2.mouseEnter({ event, edge: edge.value });
      }
      function onEdgeMouseMove(event) {
        emit2.mouseMove({ event, edge: edge.value });
      }
      function onEdgeMouseLeave(event) {
        emit2.mouseLeave({ event, edge: edge.value });
      }
      function onEdgeUpdaterSourceMouseDown(event) {
        handleEdgeUpdater(event, true);
      }
      function onEdgeUpdaterTargetMouseDown(event) {
        handleEdgeUpdater(event, false);
      }
      function onKeyDown(event) {
        var _a;
        if (!disableKeyboardA11y.value && elementSelectionKeys.includes(event.key) && isSelectable.value) {
          const unselect = event.key === "Escape";
          if (unselect) {
            (_a = edgeEl.value) == null ? void 0 : _a.blur();
            removeSelectedEdges([findEdge(props.id)]);
          } else {
            addSelectedEdges([findEdge(props.id)]);
          }
        }
      }
    }
  });
  var EdgeWrapper$1 = EdgeWrapper;
  var ConnectionLine = defineComponent({
    name: "ConnectionLine",
    compatConfig: { MODE: 3 },
    setup() {
      var _a;
      const {
        id: id2,
        connectionMode,
        connectionStartHandle,
        connectionEndHandle,
        connectionPosition,
        connectionLineType,
        connectionLineStyle,
        connectionLineOptions,
        connectionStatus,
        viewport,
        findNode
      } = useVueFlow();
      const connectionLineComponent = (_a = inject(Slots)) == null ? void 0 : _a["connection-line"];
      const fromNode = computed2(() => {
        var _a2;
        return findNode((_a2 = connectionStartHandle.value) == null ? void 0 : _a2.nodeId);
      });
      const toNode = computed2(() => {
        var _a2;
        return findNode((_a2 = connectionEndHandle.value) == null ? void 0 : _a2.nodeId) ?? null;
      });
      const toXY = computed2(() => {
        return {
          x: (connectionPosition.value.x - viewport.value.x) / viewport.value.zoom,
          y: (connectionPosition.value.y - viewport.value.y) / viewport.value.zoom
        };
      });
      const markerStart = computed2(
        () => connectionLineOptions.value.markerStart ? `url(#${getMarkerId(connectionLineOptions.value.markerStart, id2)})` : ""
      );
      const markerEnd = computed2(
        () => connectionLineOptions.value.markerEnd ? `url(#${getMarkerId(connectionLineOptions.value.markerEnd, id2)})` : ""
      );
      return () => {
        var _a2, _b, _c;
        if (!fromNode.value || !connectionStartHandle.value) {
          return null;
        }
        const startHandleId = connectionStartHandle.value.id;
        const handleType = connectionStartHandle.value.type;
        const fromHandleBounds = fromNode.value.handleBounds;
        let handleBounds = (fromHandleBounds == null ? void 0 : fromHandleBounds[handleType]) ?? [];
        if (connectionMode.value === ConnectionMode.Loose) {
          const oppositeBounds = (fromHandleBounds == null ? void 0 : fromHandleBounds[handleType === "source" ? "target" : "source"]) ?? [];
          handleBounds = [...handleBounds, ...oppositeBounds];
        }
        if (!handleBounds) {
          return null;
        }
        const fromHandle = (startHandleId ? handleBounds.find((d) => d.id === startHandleId) : handleBounds[0]) ?? null;
        const fromPosition = (fromHandle == null ? void 0 : fromHandle.position) ?? Position.Top;
        const { x: fromX, y: fromY } = getHandlePosition(fromNode.value, fromHandle, fromPosition);
        let toHandle = null;
        if (toNode.value) {
          if (connectionMode.value === ConnectionMode.Strict) {
            toHandle = ((_a2 = toNode.value.handleBounds[handleType === "source" ? "target" : "source"]) == null ? void 0 : _a2.find(
              (d) => {
                var _a3;
                return d.id === ((_a3 = connectionEndHandle.value) == null ? void 0 : _a3.id);
              }
            )) || null;
          } else {
            toHandle = ((_b = [...toNode.value.handleBounds.source ?? [], ...toNode.value.handleBounds.target ?? []]) == null ? void 0 : _b.find(
              (d) => {
                var _a3;
                return d.id === ((_a3 = connectionEndHandle.value) == null ? void 0 : _a3.id);
              }
            )) || null;
          }
        }
        const toPosition = ((_c = connectionEndHandle.value) == null ? void 0 : _c.position) ?? (fromPosition ? oppositePosition[fromPosition] : null);
        if (!fromPosition || !toPosition) {
          return null;
        }
        const type = connectionLineType.value ?? connectionLineOptions.value.type ?? ConnectionLineType.Bezier;
        let dAttr = "";
        const pathParams = {
          sourceX: fromX,
          sourceY: fromY,
          sourcePosition: fromPosition,
          targetX: toXY.value.x,
          targetY: toXY.value.y,
          targetPosition: toPosition
        };
        if (type === ConnectionLineType.Bezier) {
          [dAttr] = getBezierPath(pathParams);
        } else if (type === ConnectionLineType.Step) {
          [dAttr] = getSmoothStepPath({
            ...pathParams,
            borderRadius: 0
          });
        } else if (type === ConnectionLineType.SmoothStep) {
          [dAttr] = getSmoothStepPath(pathParams);
        } else if (type === ConnectionLineType.SimpleBezier) {
          [dAttr] = getSimpleBezierPath(pathParams);
        } else {
          dAttr = `M${fromX},${fromY} ${toXY.value.x},${toXY.value.y}`;
        }
        return h(
          "svg",
          { class: "vue-flow__edges vue-flow__connectionline vue-flow__container" },
          h(
            "g",
            { class: "vue-flow__connection" },
            connectionLineComponent ? h(connectionLineComponent, {
              sourceX: fromX,
              sourceY: fromY,
              sourcePosition: fromPosition,
              targetX: toXY.value.x,
              targetY: toXY.value.y,
              targetPosition: toPosition,
              sourceNode: fromNode.value,
              sourceHandle: fromHandle,
              targetNode: toNode.value,
              targetHandle: toHandle,
              markerEnd: markerEnd.value,
              markerStart: markerStart.value,
              connectionStatus: connectionStatus.value
            }) : h("path", {
              "d": dAttr,
              "class": [connectionLineOptions.value.class, connectionStatus.value, "vue-flow__connection-path"],
              "style": {
                ...connectionLineStyle.value,
                ...connectionLineOptions.value.style
              },
              "marker-end": markerEnd.value,
              "marker-start": markerStart.value
            })
          )
        );
      };
    }
  });
  var ConnectionLine$1 = ConnectionLine;
  var _hoisted_1$4 = ["id", "markerWidth", "markerHeight", "markerUnits", "orient"];
  var __default__$6 = {
    name: "MarkerType",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$6 = /* @__PURE__ */ defineComponent({
    ...__default__$6,
    props: {
      id: {},
      type: {},
      color: { default: "none" },
      width: { default: 12.5 },
      height: { default: 12.5 },
      markerUnits: { default: "strokeWidth" },
      orient: { default: "auto-start-reverse" },
      strokeWidth: { default: 1 }
    },
    setup(__props) {
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("marker", {
          id: _ctx.id,
          class: "vue-flow__arrowhead",
          viewBox: "-10 -10 20 20",
          refX: "0",
          refY: "0",
          markerWidth: `${_ctx.width}`,
          markerHeight: `${_ctx.height}`,
          markerUnits: _ctx.markerUnits,
          orient: _ctx.orient
        }, [
          _ctx.type === unref(MarkerType).ArrowClosed ? (openBlock(), createElementBlock("polyline", {
            key: 0,
            style: normalizeStyle({
              stroke: _ctx.color,
              fill: _ctx.color,
              strokeWidth: _ctx.strokeWidth
            }),
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            points: "-5,-4 0,0 -5,4 -5,-4"
          }, null, 4)) : createCommentVNode("", true),
          _ctx.type === unref(MarkerType).Arrow ? (openBlock(), createElementBlock("polyline", {
            key: 1,
            style: normalizeStyle({
              stroke: _ctx.color,
              strokeWidth: _ctx.strokeWidth
            }),
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            fill: "none",
            points: "-5,-4 0,0 -5,4"
          }, null, 4)) : createCommentVNode("", true)
        ], 8, _hoisted_1$4);
      };
    }
  });
  var _hoisted_1$3 = {
    class: "vue-flow__marker vue-flow__container",
    "aria-hidden": "true"
  };
  var __default__$5 = {
    name: "MarkerDefinitions",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$5 = /* @__PURE__ */ defineComponent({
    ...__default__$5,
    setup(__props) {
      const { id: vueFlowId, edges, connectionLineOptions, defaultMarkerColor: defaultColor } = useVueFlow();
      const markers = computed2(() => {
        const ids = /* @__PURE__ */ new Set();
        const markers2 = [];
        const createMarkers = (marker) => {
          if (marker) {
            const markerId = getMarkerId(marker, vueFlowId);
            if (!ids.has(markerId)) {
              if (typeof marker === "object") {
                markers2.push({ ...marker, id: markerId, color: marker.color || defaultColor.value });
              } else {
                markers2.push({ id: markerId, color: defaultColor.value, type: marker });
              }
              ids.add(markerId);
            }
          }
        };
        for (const marker of [connectionLineOptions.value.markerEnd, connectionLineOptions.value.markerStart]) {
          createMarkers(marker);
        }
        for (const edge of edges.value) {
          for (const marker of [edge.markerStart, edge.markerEnd]) {
            createMarkers(marker);
          }
        }
        return markers2.sort((a, b) => a.id.localeCompare(b.id));
      });
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("svg", _hoisted_1$3, [
          createBaseVNode("defs", null, [
            (openBlock(true), createElementBlock(Fragment, null, renderList(markers.value, (marker) => {
              return openBlock(), createBlock(_sfc_main$6, {
                id: marker.id,
                key: marker.id,
                type: marker.type,
                color: marker.color,
                width: marker.width,
                height: marker.height,
                markerUnits: marker.markerUnits,
                "stroke-width": marker.strokeWidth,
                orient: marker.orient
              }, null, 8, ["id", "type", "color", "width", "height", "markerUnits", "stroke-width", "orient"]);
            }), 128))
          ])
        ]);
      };
    }
  });
  var __default__$4 = {
    name: "Edges",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$4 = /* @__PURE__ */ defineComponent({
    ...__default__$4,
    setup(__props) {
      const { findNode, getEdges, elevateEdgesOnSelect } = useVueFlow();
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock(Fragment, null, [
          createVNode(_sfc_main$5),
          (openBlock(true), createElementBlock(Fragment, null, renderList(unref(getEdges), (edge) => {
            return openBlock(), createElementBlock("svg", {
              key: edge.id,
              class: "vue-flow__edges vue-flow__container",
              style: normalizeStyle({ zIndex: unref(getEdgeZIndex)(edge, unref(findNode), unref(elevateEdgesOnSelect)) })
            }, [
              createVNode(unref(EdgeWrapper$1), {
                id: edge.id
              }, null, 8, ["id"])
            ], 4);
          }), 128)),
          createVNode(unref(ConnectionLine$1))
        ], 64);
      };
    }
  });
  var NodeWrapper = defineComponent({
    name: "Node",
    compatConfig: { MODE: 3 },
    props: ["id", "resizeObserver"],
    setup(props) {
      const {
        id: vueFlowId,
        noPanClassName,
        selectNodesOnDrag,
        nodesSelectionActive,
        multiSelectionActive,
        emits,
        removeSelectedNodes,
        addSelectedNodes,
        updateNodeDimensions,
        onUpdateNodeInternals,
        getNodeTypes,
        nodeExtent,
        elevateNodesOnSelect,
        disableKeyboardA11y,
        ariaLiveMessage,
        snapToGrid,
        snapGrid,
        nodeDragThreshold,
        nodesDraggable,
        elementsSelectable,
        nodesConnectable,
        nodesFocusable,
        hooks
      } = useVueFlow();
      const nodeElement = ref(null);
      provide(NodeRef, nodeElement);
      provide(NodeId, props.id);
      const slots = inject(Slots);
      const instance = getCurrentInstance();
      const updateNodePositions = useUpdateNodePositions();
      const { node, parentNode } = useNode(props.id);
      const { emit: emit2, on } = useNodeHooks(node, emits);
      const isDraggable = toRef(() => typeof node.draggable === "undefined" ? nodesDraggable.value : node.draggable);
      const isSelectable = toRef(() => typeof node.selectable === "undefined" ? elementsSelectable.value : node.selectable);
      const isConnectable = toRef(() => typeof node.connectable === "undefined" ? nodesConnectable.value : node.connectable);
      const isFocusable = toRef(() => typeof node.focusable === "undefined" ? nodesFocusable.value : node.focusable);
      const hasPointerEvents = computed2(
        () => isSelectable.value || isDraggable.value || hooks.value.nodeClick.hasListeners() || hooks.value.nodeDoubleClick.hasListeners() || hooks.value.nodeMouseEnter.hasListeners() || hooks.value.nodeMouseMove.hasListeners() || hooks.value.nodeMouseLeave.hasListeners()
      );
      const isInit = toRef(() => !!node.dimensions.width && !!node.dimensions.height);
      const nodeCmp = computed2(() => {
        const name = node.type || "default";
        const slot = slots == null ? void 0 : slots[`node-${name}`];
        if (slot) {
          return slot;
        }
        let nodeType = node.template || getNodeTypes.value[name];
        if (typeof nodeType === "string") {
          if (instance) {
            const components = Object.keys(instance.appContext.components);
            if (components && components.includes(name)) {
              nodeType = resolveComponent(name, false);
            }
          }
        }
        if (nodeType && typeof nodeType !== "string") {
          return nodeType;
        }
        emits.error(new VueFlowError(ErrorCode.NODE_TYPE_MISSING, nodeType));
        return false;
      });
      const dragging = useDrag({
        id: props.id,
        el: nodeElement,
        disabled: () => !isDraggable.value,
        selectable: isSelectable,
        dragHandle: () => node.dragHandle,
        onStart(event) {
          emit2.dragStart(event);
        },
        onDrag(event) {
          emit2.drag(event);
        },
        onStop(event) {
          emit2.dragStop(event);
        },
        onClick(event) {
          onSelectNode(event);
        }
      });
      const getClass = computed2(() => node.class instanceof Function ? node.class(node) : node.class);
      const getStyle = computed2(() => {
        const styles = (node.style instanceof Function ? node.style(node) : node.style) || {};
        const width = node.width instanceof Function ? node.width(node) : node.width;
        const height = node.height instanceof Function ? node.height(node) : node.height;
        if (!styles.width && width) {
          styles.width = typeof width === "string" ? width : `${width}px`;
        }
        if (!styles.height && height) {
          styles.height = typeof height === "string" ? height : `${height}px`;
        }
        return styles;
      });
      const zIndex = toRef(() => Number(node.zIndex ?? getStyle.value.zIndex ?? 0));
      onUpdateNodeInternals((updateIds) => {
        if (updateIds.includes(props.id) || !updateIds.length) {
          updateInternals();
        }
      });
      onMounted(() => {
        watch2(
          () => node.hidden,
          (isHidden = false, _, onCleanup) => {
            if (!isHidden && nodeElement.value) {
              props.resizeObserver.observe(nodeElement.value);
              onCleanup(() => {
                if (nodeElement.value) {
                  props.resizeObserver.unobserve(nodeElement.value);
                }
              });
            }
          },
          { immediate: true, flush: "post" }
        );
      });
      watch2([() => node.type, () => node.sourcePosition, () => node.targetPosition], () => {
        nextTick(() => {
          updateNodeDimensions([{ id: props.id, nodeElement: nodeElement.value, forceUpdate: true }]);
        });
      });
      watch2(
        [
          () => node.position.x,
          () => node.position.y,
          () => {
            var _a;
            return (_a = parentNode.value) == null ? void 0 : _a.computedPosition.x;
          },
          () => {
            var _a;
            return (_a = parentNode.value) == null ? void 0 : _a.computedPosition.y;
          },
          () => {
            var _a;
            return (_a = parentNode.value) == null ? void 0 : _a.computedPosition.z;
          },
          zIndex,
          () => node.selected,
          () => node.dimensions.height,
          () => node.dimensions.width,
          () => {
            var _a;
            return (_a = parentNode.value) == null ? void 0 : _a.dimensions.height;
          },
          () => {
            var _a;
            return (_a = parentNode.value) == null ? void 0 : _a.dimensions.width;
          }
        ],
        ([newX, newY, parentX, parentY, parentZ, nodeZIndex]) => {
          const xyzPos = {
            x: newX,
            y: newY,
            z: nodeZIndex + (elevateNodesOnSelect.value ? node.selected ? 1e3 : 0 : 0)
          };
          if (typeof parentX !== "undefined" && typeof parentY !== "undefined") {
            node.computedPosition = getXYZPos({ x: parentX, y: parentY, z: parentZ }, xyzPos);
          } else {
            node.computedPosition = xyzPos;
          }
        },
        { flush: "post", immediate: true }
      );
      watch2([() => node.extent, nodeExtent], ([nodeExtent2, globalExtent], [oldNodeExtent, oldGlobalExtent]) => {
        if (nodeExtent2 !== oldNodeExtent || globalExtent !== oldGlobalExtent) {
          clampPosition2();
        }
      });
      if (node.extent === "parent" || typeof node.extent === "object" && "range" in node.extent && node.extent.range === "parent") {
        until(() => isInit).toBe(true).then(clampPosition2);
      } else {
        clampPosition2();
      }
      return () => {
        if (node.hidden) {
          return null;
        }
        return h(
          "div",
          {
            "ref": nodeElement,
            "data-id": node.id,
            "class": [
              "vue-flow__node",
              `vue-flow__node-${nodeCmp.value === false ? "default" : node.type || "default"}`,
              {
                [noPanClassName.value]: isDraggable.value,
                dragging: dragging == null ? void 0 : dragging.value,
                draggable: isDraggable.value,
                selected: node.selected,
                selectable: isSelectable.value,
                parent: node.isParent
              },
              getClass.value
            ],
            "style": {
              visibility: isInit.value ? "visible" : "hidden",
              zIndex: node.computedPosition.z ?? zIndex.value,
              transform: `translate(${node.computedPosition.x}px,${node.computedPosition.y}px)`,
              pointerEvents: hasPointerEvents.value ? "all" : "none",
              ...getStyle.value
            },
            "tabIndex": isFocusable.value ? 0 : void 0,
            "role": isFocusable.value ? "group" : void 0,
            "aria-describedby": disableKeyboardA11y.value ? void 0 : `${ARIA_NODE_DESC_KEY}-${vueFlowId}`,
            "aria-label": node.ariaLabel,
            "aria-roledescription": "node",
            ...node.domAttributes,
            "onMouseenter": onMouseEnter,
            "onMousemove": onMouseMove,
            "onMouseleave": onMouseLeave,
            "onContextmenu": onContextMenu,
            "onClick": onSelectNode,
            "onDblclick": onDoubleClick,
            "onKeydown": onKeyDown
          },
          [
            h(nodeCmp.value === false ? getNodeTypes.value.default : nodeCmp.value, {
              id: node.id,
              type: node.type,
              data: node.data,
              events: { ...node.events, ...on },
              selected: node.selected,
              resizing: node.resizing,
              dragging: dragging.value,
              connectable: isConnectable.value,
              position: node.computedPosition,
              dimensions: node.dimensions,
              isValidTargetPos: node.isValidTargetPos,
              isValidSourcePos: node.isValidSourcePos,
              parent: node.parentNode,
              parentNodeId: node.parentNode,
              zIndex: node.computedPosition.z ?? zIndex.value,
              targetPosition: node.targetPosition,
              sourcePosition: node.sourcePosition,
              label: node.label,
              dragHandle: node.dragHandle,
              onUpdateNodeInternals: updateInternals
            })
          ]
        );
      };
      function clampPosition2() {
        const nextPosition = node.computedPosition;
        const { computedPosition, position } = calcNextPosition(
          node,
          snapToGrid.value ? snapPosition(nextPosition, snapGrid.value) : nextPosition,
          emits.error,
          nodeExtent.value,
          parentNode.value
        );
        if (node.computedPosition.x !== computedPosition.x || node.computedPosition.y !== computedPosition.y) {
          node.computedPosition = { ...node.computedPosition, ...computedPosition };
        }
        if (node.position.x !== position.x || node.position.y !== position.y) {
          node.position = position;
        }
      }
      function updateInternals() {
        if (nodeElement.value) {
          updateNodeDimensions([{ id: props.id, nodeElement: nodeElement.value, forceUpdate: true }]);
        }
      }
      function onMouseEnter(event) {
        if (!(dragging == null ? void 0 : dragging.value)) {
          emit2.mouseEnter({ event, node });
        }
      }
      function onMouseMove(event) {
        if (!(dragging == null ? void 0 : dragging.value)) {
          emit2.mouseMove({ event, node });
        }
      }
      function onMouseLeave(event) {
        if (!(dragging == null ? void 0 : dragging.value)) {
          emit2.mouseLeave({ event, node });
        }
      }
      function onContextMenu(event) {
        return emit2.contextMenu({ event, node });
      }
      function onDoubleClick(event) {
        return emit2.doubleClick({ event, node });
      }
      function onSelectNode(event) {
        if (isSelectable.value && (!selectNodesOnDrag.value || !isDraggable.value || nodeDragThreshold.value > 0)) {
          handleNodeClick(
            node,
            multiSelectionActive.value,
            addSelectedNodes,
            removeSelectedNodes,
            nodesSelectionActive,
            false,
            nodeElement.value
          );
        }
        emit2.click({ event, node });
      }
      function onKeyDown(event) {
        if (isInputDOMNode(event) || disableKeyboardA11y.value) {
          return;
        }
        if (elementSelectionKeys.includes(event.key) && isSelectable.value) {
          const unselect = event.key === "Escape";
          handleNodeClick(
            node,
            multiSelectionActive.value,
            addSelectedNodes,
            removeSelectedNodes,
            nodesSelectionActive,
            unselect,
            nodeElement.value
          );
        } else if (isDraggable.value && node.selected && arrowKeyDiffs[event.key]) {
          event.preventDefault();
          ariaLiveMessage.value = `Moved selected node ${event.key.replace("Arrow", "").toLowerCase()}. New position, x: ${~~node.position.x}, y: ${~~node.position.y}`;
          updateNodePositions(
            {
              x: arrowKeyDiffs[event.key].x,
              y: arrowKeyDiffs[event.key].y
            },
            event.shiftKey
          );
        }
      }
    }
  });
  var NodeWrapper$1 = NodeWrapper;
  var _hoisted_1$2 = {
    height: "0",
    width: "0"
  };
  var __default__$3 = {
    name: "EdgeLabelRenderer",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$3 = /* @__PURE__ */ defineComponent({
    ...__default__$3,
    setup(__props) {
      const { viewportRef } = useVueFlow();
      const teleportTarget = toRef(() => {
        var _a;
        return (_a = viewportRef.value) == null ? void 0 : _a.getElementsByClassName("vue-flow__edge-labels")[0];
      });
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("svg", null, [
          (openBlock(), createElementBlock("foreignObject", _hoisted_1$2, [
            (openBlock(), createBlock(Teleport, {
              to: teleportTarget.value,
              disabled: !teleportTarget.value
            }, [
              renderSlot(_ctx.$slots, "default")
            ], 8, ["to", "disabled"]))
          ]))
        ]);
      };
    }
  });
  function useNodesInitialized(options = { includeHiddenNodes: false }) {
    const { nodes } = useVueFlow();
    return computed2(() => {
      if (nodes.value.length === 0) {
        return false;
      }
      for (const node of nodes.value) {
        if (options.includeHiddenNodes || !node.hidden) {
          if ((node == null ? void 0 : node.handleBounds) === void 0 || node.dimensions.width === 0 || node.dimensions.height === 0) {
            return false;
          }
        }
      }
      return true;
    });
  }
  var _hoisted_1$1 = { class: "vue-flow__nodes vue-flow__container" };
  var __default__$2 = {
    name: "Nodes",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$2 = /* @__PURE__ */ defineComponent({
    ...__default__$2,
    setup(__props) {
      const { getNodes, updateNodeDimensions, emits } = useVueFlow();
      const nodesInitialized = useNodesInitialized();
      const resizeObserver = ref();
      watch2(
        nodesInitialized,
        (isInit) => {
          if (isInit) {
            nextTick(() => {
              emits.nodesInitialized(getNodes.value);
            });
          }
        },
        { immediate: true }
      );
      onMounted(() => {
        resizeObserver.value = new ResizeObserver((entries) => {
          const updates = entries.map((entry) => {
            const id2 = entry.target.getAttribute("data-id");
            return {
              id: id2,
              nodeElement: entry.target,
              forceUpdate: true
            };
          });
          nextTick(() => updateNodeDimensions(updates));
        });
      });
      onBeforeUnmount(() => {
        var _a;
        return (_a = resizeObserver.value) == null ? void 0 : _a.disconnect();
      });
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", _hoisted_1$1, [
          resizeObserver.value ? (openBlock(true), createElementBlock(Fragment, { key: 0 }, renderList(unref(getNodes), (node, __, ___, _cached) => {
            const _memo = [node.id];
            if (_cached && _cached.key === node.id && isMemoSame(_cached, _memo))
              return _cached;
            const _item = (openBlock(), createBlock(unref(NodeWrapper$1), {
              id: node.id,
              key: node.id,
              "resize-observer": resizeObserver.value
            }, null, 8, ["id", "resize-observer"]));
            _item.memo = _memo;
            return _item;
          }, _cache, 0), 128)) : createCommentVNode("", true)
        ]);
      };
    }
  });
  function useStylesLoadedWarning() {
    const { emits } = useVueFlow();
    onMounted(() => {
      if (isDev()) {
        const pane = document.querySelector(".vue-flow__pane");
        if (pane && !(window.getComputedStyle(pane).zIndex === "1")) {
          emits.error(new VueFlowError(ErrorCode.MISSING_STYLES));
        }
      }
    });
  }
  var _hoisted_1 = /* @__PURE__ */ createBaseVNode("div", { class: "vue-flow__edge-labels" }, null, -1);
  var __default__$1 = {
    name: "VueFlow",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main$1 = /* @__PURE__ */ defineComponent({
    ...__default__$1,
    props: {
      id: {},
      modelValue: {},
      nodes: {},
      edges: {},
      edgeTypes: {},
      nodeTypes: {},
      connectionMode: {},
      connectionLineType: {},
      connectionLineStyle: { default: void 0 },
      connectionLineOptions: { default: void 0 },
      connectionRadius: {},
      isValidConnection: { type: [Function, null], default: void 0 },
      deleteKeyCode: { default: void 0 },
      selectionKeyCode: { type: [Boolean, null], default: void 0 },
      multiSelectionKeyCode: { default: void 0 },
      zoomActivationKeyCode: { default: void 0 },
      panActivationKeyCode: { default: void 0 },
      snapToGrid: { type: Boolean, default: void 0 },
      snapGrid: {},
      onlyRenderVisibleElements: { type: Boolean, default: void 0 },
      edgesUpdatable: { type: [Boolean, String], default: void 0 },
      nodesDraggable: { type: Boolean, default: void 0 },
      nodesConnectable: { type: Boolean, default: void 0 },
      nodeDragThreshold: {},
      elementsSelectable: { type: Boolean, default: void 0 },
      selectNodesOnDrag: { type: Boolean, default: void 0 },
      panOnDrag: { type: [Boolean, Array], default: void 0 },
      minZoom: {},
      maxZoom: {},
      defaultViewport: {},
      translateExtent: {},
      nodeExtent: {},
      defaultMarkerColor: {},
      zoomOnScroll: { type: Boolean, default: void 0 },
      zoomOnPinch: { type: Boolean, default: void 0 },
      panOnScroll: { type: Boolean, default: void 0 },
      panOnScrollSpeed: {},
      panOnScrollMode: {},
      paneClickDistance: {},
      zoomOnDoubleClick: { type: Boolean, default: void 0 },
      preventScrolling: { type: Boolean, default: void 0 },
      selectionMode: {},
      edgeUpdaterRadius: {},
      fitViewOnInit: { type: Boolean, default: void 0 },
      connectOnClick: { type: Boolean, default: void 0 },
      applyDefault: { type: Boolean, default: void 0 },
      autoConnect: { type: [Boolean, Function], default: void 0 },
      noDragClassName: {},
      noWheelClassName: {},
      noPanClassName: {},
      defaultEdgeOptions: {},
      elevateEdgesOnSelect: { type: Boolean, default: void 0 },
      elevateNodesOnSelect: { type: Boolean, default: void 0 },
      disableKeyboardA11y: { type: Boolean, default: void 0 },
      edgesFocusable: { type: Boolean, default: void 0 },
      nodesFocusable: { type: Boolean, default: void 0 },
      autoPanOnConnect: { type: Boolean, default: void 0 },
      autoPanOnNodeDrag: { type: Boolean, default: void 0 },
      autoPanSpeed: {}
    },
    emits: ["nodesChange", "edgesChange", "nodesInitialized", "paneReady", "init", "updateNodeInternals", "error", "connect", "connectStart", "connectEnd", "clickConnectStart", "clickConnectEnd", "moveStart", "move", "moveEnd", "selectionDragStart", "selectionDrag", "selectionDragStop", "selectionContextMenu", "selectionStart", "selectionEnd", "viewportChangeStart", "viewportChange", "viewportChangeEnd", "paneScroll", "paneClick", "paneContextMenu", "paneMouseEnter", "paneMouseMove", "paneMouseLeave", "edgeUpdate", "edgeContextMenu", "edgeMouseEnter", "edgeMouseMove", "edgeMouseLeave", "edgeDoubleClick", "edgeClick", "edgeUpdateStart", "edgeUpdateEnd", "nodeContextMenu", "nodeMouseEnter", "nodeMouseMove", "nodeMouseLeave", "nodeDoubleClick", "nodeClick", "nodeDragStart", "nodeDrag", "nodeDragStop", "miniMapNodeClick", "miniMapNodeDoubleClick", "miniMapNodeMouseEnter", "miniMapNodeMouseMove", "miniMapNodeMouseLeave", "update:modelValue", "update:nodes", "update:edges"],
    setup(__props, { expose: __expose, emit: emit2 }) {
      const props = __props;
      const slots = useSlots();
      const modelValue = useVModel(props, "modelValue", emit2);
      const modelNodes = useVModel(props, "nodes", emit2);
      const modelEdges = useVModel(props, "edges", emit2);
      const vfInstance = useVueFlow(props);
      const disposeWatchers = useWatchProps({ modelValue, nodes: modelNodes, edges: modelEdges }, props, vfInstance);
      useHooks(emit2, vfInstance.hooks);
      useOnInitHandler();
      useStylesLoadedWarning();
      provide(Slots, slots);
      onUnmounted(disposeWatchers);
      __expose(vfInstance);
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          ref: unref(vfInstance).vueFlowRef,
          class: "vue-flow"
        }, [
          createVNode(_sfc_main$8, null, {
            default: withCtx(() => [
              createVNode(_sfc_main$4),
              _hoisted_1,
              createVNode(_sfc_main$2),
              renderSlot(_ctx.$slots, "zoom-pane")
            ]),
            _: 3
          }),
          renderSlot(_ctx.$slots, "default"),
          createVNode(_sfc_main$7)
        ], 512);
      };
    }
  });
  var __default__ = {
    name: "Panel",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main = /* @__PURE__ */ defineComponent({
    ...__default__,
    props: {
      position: {}
    },
    setup(__props) {
      const props = __props;
      const { userSelectionActive } = useVueFlow();
      const positionClasses = computed2(() => `${props.position}`.split("-"));
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("div", {
          class: normalizeClass(["vue-flow__panel", positionClasses.value]),
          style: normalizeStyle({ pointerEvents: unref(userSelectionActive) ? "none" : "all" })
        }, [
          renderSlot(_ctx.$slots, "default")
        ], 6);
      };
    }
  });

  // node_modules/@vue-flow/background/dist/vue-flow-background.mjs
  var BackgroundVariant = /* @__PURE__ */ ((BackgroundVariant2) => {
    BackgroundVariant2["Lines"] = "lines";
    BackgroundVariant2["Dots"] = "dots";
    return BackgroundVariant2;
  })(BackgroundVariant || {});
  var LinePattern = function({ dimensions, size, color: color2 }) {
    return h("path", {
      "stroke": color2,
      "stroke-width": size,
      "d": `M${dimensions[0] / 2} 0 V${dimensions[1]} M0 ${dimensions[1] / 2} H${dimensions[0]}`
    });
  };
  var DotPattern = function({ radius, color: color2 }) {
    return h("circle", { cx: radius, cy: radius, r: radius, fill: color2 });
  };
  ({
    [BackgroundVariant.Lines]: LinePattern,
    [BackgroundVariant.Dots]: DotPattern
  });
  var DefaultBgColors = {
    [BackgroundVariant.Dots]: "#81818a",
    [BackgroundVariant.Lines]: "#eee"
  };
  var _hoisted_12 = ["id", "x", "y", "width", "height", "patternTransform"];
  var _hoisted_22 = {
    key: 2,
    height: "100",
    width: "100"
  };
  var _hoisted_32 = ["fill"];
  var _hoisted_4 = ["x", "y", "fill"];
  var __default__2 = {
    name: "Background",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main2 = /* @__PURE__ */ defineComponent({
    ...__default__2,
    props: {
      id: {},
      variant: { default: () => BackgroundVariant.Dots },
      gap: { default: 20 },
      size: { default: 1 },
      lineWidth: { default: 1 },
      patternColor: {},
      color: {},
      bgColor: {},
      height: { default: 100 },
      width: { default: 100 },
      x: { default: 0 },
      y: { default: 0 },
      offset: { default: 0 }
    },
    setup(__props) {
      const { id: vueFlowId, viewport } = useVueFlow();
      const background = computed2(() => {
        const zoom2 = viewport.value.zoom;
        const [gapX, gapY] = Array.isArray(__props.gap) ? __props.gap : [__props.gap, __props.gap];
        const scaledGap = [gapX * zoom2 || 1, gapY * zoom2 || 1];
        const scaledSize = __props.size * zoom2;
        const [offsetX, offsetY] = Array.isArray(__props.offset) ? __props.offset : [__props.offset, __props.offset];
        const scaledOffset = [offsetX * zoom2 || 1 + scaledGap[0] / 2, offsetY * zoom2 || 1 + scaledGap[1] / 2];
        return {
          scaledGap,
          offset: scaledOffset,
          size: scaledSize
        };
      });
      const patternId = toRef(() => `pattern-${vueFlowId}${__props.id ? `-${__props.id}` : ""}`);
      const patternColor = toRef(() => __props.color || __props.patternColor || DefaultBgColors[__props.variant || BackgroundVariant.Dots]);
      return (_ctx, _cache) => {
        return openBlock(), createElementBlock("svg", {
          class: "vue-flow__background vue-flow__container",
          style: normalizeStyle({
            height: `${_ctx.height > 100 ? 100 : _ctx.height}%`,
            width: `${_ctx.width > 100 ? 100 : _ctx.width}%`
          })
        }, [
          renderSlot(_ctx.$slots, "pattern-container", { id: patternId.value }, () => [
            createBaseVNode("pattern", {
              id: patternId.value,
              x: unref(viewport).x % background.value.scaledGap[0],
              y: unref(viewport).y % background.value.scaledGap[1],
              width: background.value.scaledGap[0],
              height: background.value.scaledGap[1],
              patternTransform: `translate(-${background.value.offset[0]},-${background.value.offset[1]})`,
              patternUnits: "userSpaceOnUse"
            }, [
              renderSlot(_ctx.$slots, "pattern", {}, () => [
                _ctx.variant === unref(BackgroundVariant).Lines ? (openBlock(), createBlock(unref(LinePattern), {
                  key: 0,
                  size: _ctx.lineWidth,
                  color: patternColor.value,
                  dimensions: background.value.scaledGap
                }, null, 8, ["size", "color", "dimensions"])) : _ctx.variant === unref(BackgroundVariant).Dots ? (openBlock(), createBlock(unref(DotPattern), {
                  key: 1,
                  color: patternColor.value,
                  radius: background.value.size / 2
                }, null, 8, ["color", "radius"])) : createCommentVNode("", true),
                _ctx.bgColor ? (openBlock(), createElementBlock("svg", _hoisted_22, [
                  createBaseVNode("rect", {
                    width: "100%",
                    height: "100%",
                    fill: _ctx.bgColor
                  }, null, 8, _hoisted_32)
                ])) : createCommentVNode("", true)
              ])
            ], 8, _hoisted_12)
          ]),
          createBaseVNode("rect", {
            x: _ctx.x,
            y: _ctx.y,
            width: "100%",
            height: "100%",
            fill: `url(#${patternId.value})`
          }, null, 8, _hoisted_4),
          renderSlot(_ctx.$slots, "default", { id: patternId.value })
        ], 4);
      };
    }
  });

  // node_modules/@vue-flow/controls/dist/vue-flow-controls.mjs
  var _sfc_main$12 = {
    name: "ControlButton",
    compatConfig: { MODE: 3 }
  };
  var _export_sfc = (sfc, props) => {
    const target = sfc.__vccOpts || sfc;
    for (const [key, val] of props) {
      target[key] = val;
    }
    return target;
  };
  var _hoisted_1$52 = {
    type: "button",
    class: "vue-flow__controls-button"
  };
  function _sfc_render(_ctx, _cache, $props, $setup, $data, $options) {
    return openBlock(), createElementBlock("button", _hoisted_1$52, [
      renderSlot(_ctx.$slots, "default")
    ]);
  }
  var ControlButton = /* @__PURE__ */ _export_sfc(_sfc_main$12, [["render", _sfc_render]]);
  var _hoisted_1$42 = {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 32 32"
  };
  var _hoisted_2$4 = /* @__PURE__ */ createBaseVNode("path", { d: "M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" }, null, -1);
  var _hoisted_3$4 = [
    _hoisted_2$4
  ];
  function render$4(_ctx, _cache) {
    return openBlock(), createElementBlock("svg", _hoisted_1$42, _hoisted_3$4);
  }
  var PlusIcon = { render: render$4 };
  var _hoisted_1$32 = {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 32 5"
  };
  var _hoisted_2$3 = /* @__PURE__ */ createBaseVNode("path", { d: "M0 0h32v4.2H0z" }, null, -1);
  var _hoisted_3$3 = [
    _hoisted_2$3
  ];
  function render$3(_ctx, _cache) {
    return openBlock(), createElementBlock("svg", _hoisted_1$32, _hoisted_3$3);
  }
  var MinusIcon = { render: render$3 };
  var _hoisted_1$22 = {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 32 30"
  };
  var _hoisted_2$22 = /* @__PURE__ */ createBaseVNode("path", { d: "M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0 0 27.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94a.919.919 0 0 1-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z" }, null, -1);
  var _hoisted_3$2 = [
    _hoisted_2$22
  ];
  function render$2(_ctx, _cache) {
    return openBlock(), createElementBlock("svg", _hoisted_1$22, _hoisted_3$2);
  }
  var FitView = { render: render$2 };
  var _hoisted_1$12 = {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 25 32"
  };
  var _hoisted_2$12 = /* @__PURE__ */ createBaseVNode("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0 8 0 4.571 3.429 4.571 7.619v3.048H3.048A3.056 3.056 0 0 0 0 13.714v15.238A3.056 3.056 0 0 0 3.048 32h18.285a3.056 3.056 0 0 0 3.048-3.048V13.714a3.056 3.056 0 0 0-3.048-3.047zM12.19 24.533a3.056 3.056 0 0 1-3.047-3.047 3.056 3.056 0 0 1 3.047-3.048 3.056 3.056 0 0 1 3.048 3.048 3.056 3.056 0 0 1-3.048 3.047zm4.724-13.866H7.467V7.619c0-2.59 2.133-4.724 4.723-4.724 2.591 0 4.724 2.133 4.724 4.724v3.048z" }, null, -1);
  var _hoisted_3$12 = [
    _hoisted_2$12
  ];
  function render$1(_ctx, _cache) {
    return openBlock(), createElementBlock("svg", _hoisted_1$12, _hoisted_3$12);
  }
  var Lock = { render: render$1 };
  var _hoisted_13 = {
    xmlns: "http://www.w3.org/2000/svg",
    viewBox: "0 0 25 32"
  };
  var _hoisted_23 = /* @__PURE__ */ createBaseVNode("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0c-4.114 1.828-1.37 2.133.305 2.438 1.676.305 4.42 2.59 4.42 5.181v3.048H3.047A3.056 3.056 0 0 0 0 13.714v15.238A3.056 3.056 0 0 0 3.048 32h18.285a3.056 3.056 0 0 0 3.048-3.048V13.714a3.056 3.056 0 0 0-3.048-3.047zM12.19 24.533a3.056 3.056 0 0 1-3.047-3.047 3.056 3.056 0 0 1 3.047-3.048 3.056 3.056 0 0 1 3.048 3.048 3.056 3.056 0 0 1-3.048 3.047z" }, null, -1);
  var _hoisted_33 = [
    _hoisted_23
  ];
  function render(_ctx, _cache) {
    return openBlock(), createElementBlock("svg", _hoisted_13, _hoisted_33);
  }
  var Unlock = { render };
  var __default__3 = {
    name: "Controls",
    compatConfig: { MODE: 3 }
  };
  var _sfc_main3 = /* @__PURE__ */ defineComponent({
    ...__default__3,
    props: {
      showZoom: { type: Boolean, default: true },
      showFitView: { type: Boolean, default: true },
      showInteractive: { type: Boolean, default: true },
      fitViewParams: {},
      position: { default: () => PanelPosition.BottomLeft }
    },
    emits: ["zoomIn", "zoomOut", "fitView", "interactionChange"],
    setup(__props, { emit: emit2 }) {
      const {
        nodesDraggable,
        nodesConnectable,
        elementsSelectable,
        setInteractive,
        zoomIn,
        zoomOut,
        fitView,
        viewport,
        minZoom,
        maxZoom
      } = useVueFlow();
      const isInteractive = toRef(() => nodesDraggable.value || nodesConnectable.value || elementsSelectable.value);
      const minZoomReached = toRef(() => viewport.value.zoom <= minZoom.value);
      const maxZoomReached = toRef(() => viewport.value.zoom >= maxZoom.value);
      function onZoomInHandler() {
        zoomIn();
        emit2("zoomIn");
      }
      function onZoomOutHandler() {
        zoomOut();
        emit2("zoomOut");
      }
      function onFitViewHandler() {
        fitView(__props.fitViewParams);
        emit2("fitView");
      }
      function onInteractiveChangeHandler() {
        setInteractive(!isInteractive.value);
        emit2("interactionChange", !isInteractive.value);
      }
      return (_ctx, _cache) => {
        return openBlock(), createBlock(unref(_sfc_main), {
          class: "vue-flow__controls",
          position: _ctx.position
        }, {
          default: withCtx(() => [
            renderSlot(_ctx.$slots, "top"),
            _ctx.showZoom ? (openBlock(), createElementBlock(Fragment, { key: 0 }, [
              renderSlot(_ctx.$slots, "control-zoom-in", {}, () => [
                createVNode(ControlButton, {
                  class: "vue-flow__controls-zoomin",
                  disabled: maxZoomReached.value,
                  onClick: onZoomInHandler
                }, {
                  default: withCtx(() => [
                    renderSlot(_ctx.$slots, "icon-zoom-in", {}, () => [
                      (openBlock(), createBlock(resolveDynamicComponent(unref(PlusIcon))))
                    ])
                  ]),
                  _: 3
                }, 8, ["disabled"])
              ]),
              renderSlot(_ctx.$slots, "control-zoom-out", {}, () => [
                createVNode(ControlButton, {
                  class: "vue-flow__controls-zoomout",
                  disabled: minZoomReached.value,
                  onClick: onZoomOutHandler
                }, {
                  default: withCtx(() => [
                    renderSlot(_ctx.$slots, "icon-zoom-out", {}, () => [
                      (openBlock(), createBlock(resolveDynamicComponent(unref(MinusIcon))))
                    ])
                  ]),
                  _: 3
                }, 8, ["disabled"])
              ])
            ], 64)) : createCommentVNode("", true),
            _ctx.showFitView ? renderSlot(_ctx.$slots, "control-fit-view", { key: 1 }, () => [
              createVNode(ControlButton, {
                class: "vue-flow__controls-fitview",
                onClick: onFitViewHandler
              }, {
                default: withCtx(() => [
                  renderSlot(_ctx.$slots, "icon-fit-view", {}, () => [
                    (openBlock(), createBlock(resolveDynamicComponent(unref(FitView))))
                  ])
                ]),
                _: 3
              })
            ]) : createCommentVNode("", true),
            _ctx.showInteractive ? renderSlot(_ctx.$slots, "control-interactive", { key: 2 }, () => [
              _ctx.showInteractive ? (openBlock(), createBlock(ControlButton, {
                key: 0,
                class: "vue-flow__controls-interactive",
                onClick: onInteractiveChangeHandler
              }, {
                default: withCtx(() => [
                  isInteractive.value ? renderSlot(_ctx.$slots, "icon-unlock", { key: 0 }, () => [
                    (openBlock(), createBlock(resolveDynamicComponent(unref(Unlock))))
                  ]) : createCommentVNode("", true),
                  !isInteractive.value ? renderSlot(_ctx.$slots, "icon-lock", { key: 1 }, () => [
                    (openBlock(), createBlock(resolveDynamicComponent(unref(Lock))))
                  ]) : createCommentVNode("", true)
                ]),
                _: 3
              })) : createCommentVNode("", true)
            ]) : createCommentVNode("", true),
            renderSlot(_ctx.$slots, "default")
          ]),
          _: 3
        }, 8, ["position"]);
      };
    }
  });

  // src/lib/dsl.js
  var NODE_TYPES = [
    "start",
    "python",
    "agent",
    "model",
    "condition",
    "join",
    "approval",
    "end"
  ];
  var NODE_LABELS = {
    start: "开始",
    python: "Python 工具",
    agent: "Agent",
    model: "模型",
    condition: "条件分支",
    join: "并行汇合",
    approval: "人工审批",
    end: "结束"
  };
  var REPEATABLE_NODE_TYPES = [
    "python",
    "agent",
    "model",
    "condition",
    "join",
    "approval"
  ];
  var CONDITION_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"];
  var MODEL_RESPONSE_MODES = ["json", "text"];
  function emptySchema() {
    return { type: "object", properties: {} };
  }
  function defaultPolicy() {
    return {
      budget_limit: "1.00",
      currency: "USD",
      max_active_seconds: 300,
      max_child_calls: 0,
      max_child_depth: 0
    };
  }
  function conditionPorts(config) {
    const ports = [];
    const seen = /* @__PURE__ */ new Set();
    const add = (port) => {
      if (typeof port === "string" && port !== "" && !seen.has(port)) {
        seen.add(port);
        ports.push(port);
      }
    };
    const cases = config && Array.isArray(config.cases) ? config.cases : [];
    for (const item of cases) {
      if (item && typeof item === "object") {
        add(item.port);
      }
    }
    if (config && typeof config === "object") {
      add(config.default_port);
    }
    return ports;
  }
  function defaultNode(type, id2) {
    const base = {
      id: id2,
      type,
      inputs: {},
      output_schema: emptySchema(),
      config: {}
    };
    if (type === "python") {
      base.config = { tool_version: "" };
    } else if (type === "agent") {
      base.config = {
        instructions: "",
        max_turns: 8,
        model_profile_version: "",
        subapp_versions: [],
        tool_versions: []
      };
    } else if (type === "model") {
      base.config = {
        model_profile_version: "",
        prompt_template: "",
        response_mode: "json"
      };
    } else if (type === "condition") {
      base.config = {
        join_id: "",
        default_port: "no",
        cases: [{ input: "", op: "eq", value: true, port: "yes" }]
      };
    } else if (type === "join") {
      base.config = {
        mode: "all_active",
        fork_id: "",
        required_sources: [],
        optional_sources: []
      };
    } else if (type === "approval") {
      base.inputs = { proposal: { literal: {} } };
      base.config = { title: "", expires_seconds: 3600 };
    }
    return base;
  }
  function defaultDefinition() {
    return {
      dsl_version: "1.0",
      input_schema: emptySchema(),
      output_schema: emptySchema(),
      nodes: [
        defaultNode("start", "start"),
        defaultNode("python", "python1"),
        defaultNode("agent", "agent1"),
        defaultNode("end", "end")
      ],
      edges: [
        { source: "start", target: "python1" },
        { source: "python1", target: "agent1" },
        { source: "agent1", target: "end" }
      ],
      policy: defaultPolicy()
    };
  }
  var DEFAULT_POSITIONS = {
    start: { x: 40, y: 160 },
    python: { x: 280, y: 160 },
    agent: { x: 520, y: 160 },
    model: { x: 400, y: 320 },
    condition: { x: 280, y: 320 },
    join: { x: 600, y: 320 },
    approval: { x: 420, y: 440 },
    end: { x: 760, y: 160 }
  };
  function defaultLayout(definition) {
    const source = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
    const positions = {};
    let middle = 0;
    for (const node of source) {
      if (DEFAULT_POSITIONS[node.type]) {
        positions[node.id] = { ...DEFAULT_POSITIONS[node.type] };
      } else {
        positions[node.id] = { x: 280 + 240 * middle, y: 160 };
        middle += 1;
      }
    }
    return { version: 1, positions };
  }
  function positionsOf(definition, layout) {
    const stored = layout && typeof layout.positions === "object" && layout.positions !== null ? layout.positions : {};
    const result = {};
    let middle = 0;
    for (const node of definition.nodes || []) {
      const position = stored[node.id];
      const valid = position && typeof position.x === "number" && typeof position.y === "number";
      if (valid) {
        result[node.id] = { x: position.x, y: position.y };
      } else if (node.type === "start" || node.type === "end") {
        result[node.id] = { ...DEFAULT_POSITIONS[node.type] };
      } else {
        result[node.id] = { x: 280 + 240 * middle, y: 160 };
        middle += 1;
      }
    }
    return result;
  }
  function edgeIdFor(edge, index) {
    return String(edge && edge.source) + "->" + String(edge && edge.target) + ":" + index;
  }
  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }
  function configOf(node) {
    return node && node.config && typeof node.config === "object" && !Array.isArray(node.config) ? node.config : {};
  }
  function typeOfNode(node) {
    return node && node.data && node.data.nodeType || node && node.type || "";
  }
  function definitionToGraph(definition, layout) {
    const positions = positionsOf(definition, layout);
    const sourceNodes = definition.nodes || [];
    const sourceEdges = definition.edges || [];
    const typeById = new Map(sourceNodes.map((node) => [node.id, node.type]));
    const incoming = /* @__PURE__ */ new Map();
    for (const edge of sourceEdges) {
      if (!incoming.has(edge.target)) {
        incoming.set(edge.target, []);
      }
      incoming.get(edge.target).push(edge.source);
    }
    const nodes = sourceNodes.map((node) => {
      const type = node.type;
      const data = {
        label: NODE_LABELS[type] || type,
        nodeType: type,
        config: clone(configOf(node))
      };
      if (type === "condition") {
        data.ports = conditionPorts(configOf(node));
      }
      if (type === "join") {
        data.sourceLabels = [...incoming.get(node.id) || []];
      }
      return {
        id: node.id,
        type: "default",
        label: NODE_LABELS[type] || type,
        class: "orchara-node orchara-node-" + type,
        position: positions[node.id] || { x: 0, y: 0 },
        data
      };
    });
    const edges = sourceEdges.map((edge, index) => {
      const port = typeof edge.port === "string" && edge.port !== "" ? edge.port : "";
      const isConditionSource = typeById.get(edge.source) === "condition";
      const handle = isConditionSource ? port : "";
      return {
        id: edgeIdFor(edge, index),
        source: edge.source,
        target: edge.target,
        class: "orchara-edge" + (port ? " orchara-edge-port" : ""),
        label: handle,
        sourceHandle: handle || void 0,
        targetHandle: "in",
        data: { port }
      };
    });
    return { nodes, edges };
  }
  function topologicalOrder(items, edges, idOf) {
    const ids = items.map(idOf);
    const index = new Map(ids.map((id2, position) => [id2, position]));
    const adjacency = new Map(ids.map((id2) => [id2, []]));
    const indegree = new Map(ids.map((id2) => [id2, 0]));
    for (const edge of edges || []) {
      if (!index.has(edge.source) || !index.has(edge.target)) {
        continue;
      }
      const targets = adjacency.get(edge.source);
      if (targets.includes(edge.target)) {
        continue;
      }
      targets.push(edge.target);
      indegree.set(edge.target, indegree.get(edge.target) + 1);
    }
    for (const targets of adjacency.values()) {
      targets.sort((a, b) => index.get(a) - index.get(b));
    }
    const queue2 = ids.filter((id2) => indegree.get(id2) === 0);
    const order = [];
    while (queue2.length > 0) {
      const current = queue2.shift();
      order.push(current);
      for (const target of adjacency.get(current)) {
        indegree.set(target, indegree.get(target) - 1);
        if (indegree.get(target) === 0) {
          queue2.push(target);
        }
      }
    }
    return order.length === ids.length ? order : ids;
  }
  function graphToDefinition(graphNodes, graphEdges, previous) {
    const previousById = new Map(
      (previous && previous.nodes ? previous.nodes : []).map((node) => [node.id, node])
    );
    const sourceNodes = Array.isArray(graphNodes) ? graphNodes : [];
    const sourceEdges = Array.isArray(graphEdges) ? graphEdges : [];
    const typeById = new Map(sourceNodes.map((node) => [node.id, typeOfNode(node)]));
    const byId = /* @__PURE__ */ new Map();
    for (const flowNode of sourceNodes) {
      const id2 = flowNode.id;
      const nodeType = typeOfNode(flowNode) || previousById.get(id2)?.type || "python";
      const old = previousById.get(id2) || defaultNode(nodeType, id2);
      byId.set(id2, {
        id: id2,
        type: nodeType,
        // Deep-copy the parts merged by id: the rebuilt definition must not alias
        // the previous one the page still holds in its textareas.
        inputs: old.inputs && typeof old.inputs === "object" ? clone(old.inputs) : {},
        output_schema: old.output_schema && typeof old.output_schema === "object" ? clone(old.output_schema) : emptySchema(),
        config: old.config && typeof old.config === "object" ? clone(old.config) : {}
      });
    }
    const draft = {
      ...previous || {},
      dsl_version: previous && previous.dsl_version || "1.0",
      input_schema: previous && previous.input_schema || emptySchema(),
      output_schema: previous && previous.output_schema || emptySchema(),
      policy: previous && previous.policy || defaultPolicy(),
      nodes: [],
      edges: []
    };
    const order = chainOrderFromGraph(sourceNodes, sourceEdges);
    for (const id2 of order) {
      if (byId.has(id2)) {
        draft.nodes.push(byId.get(id2));
        byId.delete(id2);
      }
    }
    for (const node of byId.values()) {
      draft.nodes.push(node);
    }
    const known = new Set(draft.nodes.map((node) => node.id));
    for (const node of draft.nodes) {
      if (node.type !== "join" || !node.config || typeof node.config !== "object") {
        continue;
      }
      const required = Array.isArray(node.config.required_sources) ? node.config.required_sources : [];
      const optional = Array.isArray(node.config.optional_sources) ? node.config.optional_sources : [];
      const keptRequired = required.filter((id2) => known.has(id2) && id2 !== node.id);
      const keptOptional = optional.filter(
        (id2) => known.has(id2) && id2 !== node.id && !keptRequired.includes(id2)
      );
      node.config = {
        ...node.config,
        required_sources: [...new Set(keptRequired)],
        optional_sources: [...new Set(keptOptional)]
      };
    }
    for (const edge of sourceEdges) {
      if (known.has(edge.source) && known.has(edge.target)) {
        const out = { source: edge.source, target: edge.target };
        let port = "";
        if (edge.data && typeof edge.data.port === "string") {
          port = edge.data.port;
        }
        if (port === "" && typeById.get(edge.source) === "condition" && typeof edge.sourceHandle === "string" && edge.sourceHandle !== "out") {
          port = edge.sourceHandle;
        }
        if (port !== "") {
          out.port = port;
        }
        draft.edges.push(out);
      }
    }
    return draft;
  }
  function chainOrderFromGraph(nodes, edges) {
    return topologicalOrder(nodes || [], edges || [], (node) => node.id);
  }
  function layoutFromGraph(graphNodes, previousLayout) {
    const base = previousLayout && typeof previousLayout === "object" ? previousLayout : {};
    const positions = { ...base.positions || {} };
    for (const node of graphNodes || []) {
      const position = node.position || (node.computedPosition || null);
      if (position && typeof position.x === "number" && typeof position.y === "number") {
        positions[node.id] = { x: position.x, y: position.y };
      }
    }
    return { ...base, version: 1, positions };
  }
  function flowConfig(node) {
    return node && node.data && node.data.config && typeof node.data.config === "object" ? node.data.config : {};
  }
  function declaredForkIds(nodes) {
    const declared = /* @__PURE__ */ new Set();
    for (const node of nodes || []) {
      if (typeOfNode(node) !== "join") {
        continue;
      }
      const forkId = flowConfig(node).fork_id;
      if (typeof forkId === "string" && forkId !== "") {
        declared.add(forkId);
      }
    }
    return declared;
  }
  function edgePortOf(edge) {
    if (edge && edge.data && typeof edge.data.port === "string" && edge.data.port !== "") {
      return edge.data.port;
    }
    if (edge && typeof edge.sourceHandle === "string" && edge.sourceHandle !== "" && edge.sourceHandle !== "out") {
      return edge.sourceHandle;
    }
    return "";
  }
  function connectionProblem(nodes, edges, connection) {
    const source = connection && connection.source;
    const target = connection && connection.target;
    if (!source || !target) {
      return { code: "invalid_connection", reason: "连接缺少起点或终点。" };
    }
    if (source === target) {
      return { code: "self_loop", reason: "节点不能连接到自己。" };
    }
    const nodeList = nodes || [];
    const edgeList = edges || [];
    const byId = new Map(nodeList.map((node) => [node.id, node]));
    const sourceNode = byId.get(source);
    const targetNode = byId.get(target);
    if (!sourceNode || !targetNode) {
      return { code: "unknown_node", reason: "连接引用了不存在的节点。" };
    }
    const sourceType2 = typeOfNode(sourceNode);
    const targetType = typeOfNode(targetNode);
    if (sourceType2 === "end") {
      return { code: "end_has_no_output", reason: "结束节点没有输出，不能连出。" };
    }
    if (targetType === "start") {
      return { code: "start_has_no_input", reason: "开始节点没有输入，不能连入。" };
    }
    const requested = typeof connection.sourceHandle === "string" && connection.sourceHandle !== "" && connection.sourceHandle !== "out" ? connection.sourceHandle : typeof connection.port === "string" ? connection.port : "";
    const outgoing = edgeList.filter((edge) => edge.source === source);
    const sameTarget = edgeList.filter((edge) => edge.source === source && edge.target === target);
    let port = "";
    if (sourceType2 === "condition") {
      const allowed = conditionPorts(flowConfig(sourceNode));
      if (requested !== "") {
        if (!allowed.includes(requested)) {
          return {
            code: "bad_port",
            reason: '端口 "' + requested + '" 不在该条件节点的 cases/default_port 中；可用端口：' + (allowed.length ? allowed.join("、") : "（无）") + "。"
          };
        }
        port = requested;
      } else {
        const used = new Set(outgoing.map(edgePortOf).filter((value) => value !== ""));
        port = allowed.find((candidate) => !used.has(candidate)) || "";
        if (port === "") {
          return {
            code: "no_free_port",
            reason: "该条件节点的所有端口都已连线；请新增 case 端口或删除一条出边。"
          };
        }
      }
      if (outgoing.some((edge) => edgePortOf(edge) === port)) {
        return {
          code: "port_exists",
          reason: '端口 "' + port + '" 已经用于另一条出边了；同一个条件端口只能连接一条边。'
        };
      }
    } else if (sameTarget.length > 0) {
      return { code: "duplicate_edge", reason: "这条连接已经存在。" };
    }
    const incoming = edgeList.filter((edge) => edge.target === target);
    if (incoming.length >= 1 && targetType !== "join") {
      return {
        code: "merge_without_join",
        reason: "目标节点 " + target + " 已有输入，只有 join 可以多入；请先创建配对 join 合并分支。"
      };
    }
    if (sourceType2 !== "condition") {
      const declared = declaredForkIds(nodeList);
      if (outgoing.length >= 1 && !declared.has(source)) {
        return {
          code: "fork_requires_join",
          reason: "多出口需要唯一配对 join 声明 fork：请先创建 join 并把 fork_id 设为 " + source + "，或只保留一条出边。"
        };
      }
    }
    if (createsCycle(edgeList, source, target)) {
      return { code: "cycle", reason: "这条连接会形成环，工作流必须是无环图。" };
    }
    return { ok: true, code: "ok", reason: "", port: sourceType2 === "condition" ? port : "" };
  }
  function canConnect(nodes, edges, connection) {
    const problem2 = connectionProblem(nodes, edges, connection);
    return problem2.ok === true ? problem2 : { ...problem2, ok: false };
  }
  function createsCycle(edges, source, target) {
    const adjacency = /* @__PURE__ */ new Map();
    for (const edge of edges || []) {
      if (!adjacency.has(edge.source)) {
        adjacency.set(edge.source, []);
      }
      adjacency.get(edge.source).push(edge.target);
    }
    const pending = [target];
    const seen = /* @__PURE__ */ new Set();
    while (pending.length > 0) {
      const current = pending.pop();
      if (current === source) {
        return true;
      }
      if (seen.has(current)) {
        continue;
      }
      seen.add(current);
      for (const next of adjacency.get(current) || []) {
        pending.push(next);
      }
    }
    return false;
  }
  function buildGraph(nodes, edges) {
    const index = /* @__PURE__ */ new Map();
    nodes.forEach((node, position) => index.set(node.id, position));
    const adjacency = new Map(nodes.map((node) => [node.id, /* @__PURE__ */ new Set()]));
    const reverse = new Map(nodes.map((node) => [node.id, /* @__PURE__ */ new Set()]));
    for (const edge of edges || []) {
      if (!adjacency.has(edge.source) || !adjacency.has(edge.target)) {
        continue;
      }
      adjacency.get(edge.source).add(edge.target);
      reverse.get(edge.target).add(edge.source);
    }
    const indegree = new Map(nodes.map((node) => [node.id, reverse.get(node.id).size]));
    const queue2 = nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id);
    const topo = [];
    while (queue2.length > 0) {
      const current = queue2.shift();
      topo.push(current);
      const targets = [...adjacency.get(current)].sort((a, b) => index.get(a) - index.get(b));
      for (const target of targets) {
        indegree.set(target, indegree.get(target) - 1);
        if (indegree.get(target) === 0) {
          queue2.push(target);
        }
      }
    }
    const dom = /* @__PURE__ */ new Map();
    const pdom = /* @__PURE__ */ new Map();
    if (topo.length === nodes.length) {
      for (const id2 of topo) {
        const parents = [...reverse.get(id2)];
        if (parents.length === 0) {
          dom.set(id2, /* @__PURE__ */ new Set([id2]));
          continue;
        }
        let common = null;
        for (const parent of parents) {
          const values = dom.get(parent);
          common = common === null ? new Set(values) : new Set([...common].filter((value) => values.has(value)));
        }
        common.add(id2);
        dom.set(id2, common);
      }
      for (const id2 of [...topo].reverse()) {
        const children2 = [...adjacency.get(id2)];
        if (children2.length === 0) {
          pdom.set(id2, /* @__PURE__ */ new Set([id2]));
          continue;
        }
        let common = null;
        for (const child of children2) {
          const values = pdom.get(child);
          common = common === null ? new Set(values) : new Set([...common].filter((value) => values.has(value)));
        }
        common.add(id2);
        pdom.set(id2, common);
      }
    }
    return { index, adjacency, reverse, topo, dom, pdom };
  }
  function reachableFrom(adjacency, id2) {
    const seen = /* @__PURE__ */ new Set();
    const pending = [id2];
    while (pending.length > 0) {
      const current = pending.pop();
      for (const next of adjacency.get(current) || []) {
        if (!seen.has(next)) {
          seen.add(next);
          pending.push(next);
        }
      }
    }
    return seen;
  }
  function reaches(adjacency, id2, target) {
    return reachableFrom(adjacency, id2).has(target);
  }
  function regionPairs(definition) {
    const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
    const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
    if (nodes.length === 0) {
      return [];
    }
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const graph = buildGraph(nodes, edges);
    const complete = graph.topo.length === nodes.length;
    const pairs = [];
    for (const joinNode of nodes) {
      if (joinNode.type !== "join") {
        continue;
      }
      let forkId = configOf(joinNode).fork_id;
      if (typeof forkId !== "string" || forkId === "" || !byId.has(forkId)) {
        const pairedCondition = nodes.find(
          (node) => node.type === "condition" && configOf(node).join_id === joinNode.id
        );
        forkId = pairedCondition ? pairedCondition.id : "";
      }
      if (forkId === "" || !byId.has(forkId) || forkId === joinNode.id) {
        continue;
      }
      const inside = /* @__PURE__ */ new Set();
      for (const node of nodes) {
        if (node.id === forkId || node.id === joinNode.id) {
          continue;
        }
        let isInside;
        if (complete && graph.dom.has(node.id) && graph.pdom.has(node.id)) {
          isInside = graph.dom.get(node.id).has(forkId) && graph.pdom.get(node.id).has(joinNode.id);
        } else {
          isInside = reaches(graph.adjacency, forkId, node.id) && reaches(graph.reverse, joinNode.id, node.id);
        }
        if (isInside) {
          inside.add(node.id);
        }
      }
      pairs.push({ forkId, joinId: joinNode.id, inside });
    }
    return pairs;
  }
  function dominanceOf(definition) {
    const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
    const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
    if (nodes.length === 0) {
      return null;
    }
    const graph = buildGraph(nodes, edges);
    if (graph.topo.length !== nodes.length) {
      return null;
    }
    return graph.dom;
  }
  function branchNodeIds(definition) {
    const branch = /* @__PURE__ */ new Set();
    for (const pair of regionPairs(definition)) {
      for (const id2 of pair.inside) {
        branch.add(id2);
      }
    }
    return branch;
  }
  function problem(code, pointer2, message, extra) {
    return {
      code,
      pointer: pointer2 || "",
      message,
      nodeId: extra && extra.nodeId ? extra.nodeId : "",
      edgeIndex: extra && typeof extra.edgeIndex === "number" ? extra.edgeIndex : null
    };
  }
  function sortedStrings(values) {
    return [...values].sort();
  }
  function sameList(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  function invalidDefinition() {
    return [problem("bad_document", "", "画布定义必须包含 nodes 与 edges 数组。")];
  }
  function regionProblems(definition) {
    const problems = [];
    if (!definition || typeof definition !== "object" || !Array.isArray(definition.nodes) || !Array.isArray(definition.edges)) {
      return invalidDefinition();
    }
    const nodes = definition.nodes;
    const edges = definition.edges;
    const seen = /* @__PURE__ */ new Set();
    let identityBroken = false;
    nodes.forEach((node, position) => {
      const id2 = node && node.id;
      if (typeof id2 !== "string" || id2 === "") {
        problems.push(problem("bad_id", "/nodes/" + position, "节点缺少合法的 id。"));
        identityBroken = true;
        return;
      }
      if (seen.has(id2)) {
        problems.push(problem(
          "duplicate_id",
          "/nodes/" + position,
          '节点 id "' + id2 + '" 重复了；每个节点必须有唯一 id。',
          { nodeId: id2 }
        ));
        identityBroken = true;
      }
      seen.add(id2);
    });
    edges.forEach((edge, position) => {
      if (!edge || !seen.has(edge.source) || !seen.has(edge.target)) {
        problems.push(problem(
          "unknown_endpoint",
          "/edges/" + position,
          "边 #" + position + " 引用了不存在的节点。",
          {
            nodeId: edge && seen.has(edge.source) ? edge.source : "",
            edgeIndex: position
          }
        ));
        identityBroken = true;
      }
    });
    if (identityBroken) {
      return problems;
    }
    const starts = nodes.filter((node) => node.type === "start");
    const ends = nodes.filter((node) => node.type === "end");
    if (starts.length !== 1 || ends.length !== 1) {
      problems.push(problem(
        "start_end_count",
        "",
        "画布必须恰好有一个开始节点和一个结束节点（当前 " + starts.length + " 个开始、" + ends.length + " 个结束）。"
      ));
      return problems;
    }
    const graph = buildGraph(nodes, edges);
    if (graph.topo.length !== nodes.length) {
      problems.push(problem("cycle", "", "连接形成了环；工作流必须是有向无环图。"));
      return problems;
    }
    const reachable = reachableFrom(graph.adjacency, starts[0].id);
    const canReachEnd = reachableFrom(graph.reverse, ends[0].id);
    nodes.forEach((node, position) => {
      if (node.id !== starts[0].id && !reachable.has(node.id)) {
        problems.push(problem(
          "unreachable_node",
          "/nodes/" + position,
          "节点 " + node.id + " 无法从开始节点到达。",
          { nodeId: node.id }
        ));
      }
      if (node.id !== ends[0].id && !canReachEnd.has(node.id)) {
        problems.push(problem(
          "cannot_reach_end",
          "/nodes/" + position,
          "节点 " + node.id + " 无法到达结束节点。",
          { nodeId: node.id }
        ));
      }
      if (graph.reverse.get(node.id).size >= 2 && node.type !== "join") {
        problems.push(problem(
          "merge_without_join",
          "/nodes/" + position,
          "节点 " + node.id + " 有多个上游分支；需要先创建配对 join 汇合，不能直接在普通节点合并。",
          { nodeId: node.id }
        ));
      }
    });
    if (problems.length > 0) {
      return problems;
    }
    const index = graph.index;
    const declaredForks = /* @__PURE__ */ new Set();
    for (const node of nodes) {
      if (node.type !== "join") {
        continue;
      }
      const forkId = configOf(node).fork_id;
      if (typeof forkId === "string" && forkId !== "") {
        declaredForks.add(forkId);
      }
    }
    nodes.forEach((node, position) => {
      if (node.type !== "condition") {
        return;
      }
      const joinId = configOf(node).join_id;
      const joinPosition = index.get(joinId);
      let valid = joinPosition !== void 0;
      if (valid) {
        const joinNode = nodes[joinPosition];
        if (joinNode.type !== "join" || configOf(joinNode).fork_id !== node.id) {
          valid = false;
        }
      }
      if (!valid) {
        problems.push(problem(
          "invalid_region",
          "/nodes/" + position + "/config/join_id",
          "条件节点 " + node.id + " 的 join_id 必须指向唯一配对 join，且该 join 的 fork_id 必须指回 " + node.id + "（双向配对）。",
          { nodeId: node.id }
        ));
      }
    });
    nodes.forEach((node, position) => {
      if (node.type !== "join") {
        return;
      }
      const at = "/nodes/" + position + "/config/fork_id";
      const forkId = configOf(node).fork_id;
      const forkPosition = index.get(forkId);
      if (forkPosition === void 0 || forkId === node.id) {
        problems.push(problem(
          "invalid_region",
          at,
          "join " + node.id + " 的 fork_id 必须指向一个真实存在的分叉节点。",
          { nodeId: node.id }
        ));
        return;
      }
      const forkNode = nodes[forkPosition];
      if (forkNode.type === "condition") {
        if (configOf(forkNode).join_id !== node.id) {
          problems.push(problem(
            "invalid_region",
            at,
            "join " + node.id + " 声明 fork_id=" + forkId + "，但该条件节点的 join_id 没有指回 " + node.id + "（双向配对）。",
            { nodeId: node.id }
          ));
        }
      } else if (graph.adjacency.get(forkId).size < 2) {
        problems.push(problem(
          "invalid_region",
          at,
          "join " + node.id + " 声明的 fork_id=" + forkId + " 只有一条出边；普通 fork 必须至少分出两条分支。",
          { nodeId: node.id }
        ));
      }
    });
    nodes.forEach((node, position) => {
      if (node.type === "condition" || node.type === "join") {
        return;
      }
      if (graph.adjacency.get(node.id).size >= 2 && !declaredForks.has(node.id)) {
        problems.push(problem(
          "invalid_region",
          "/nodes/" + position,
          "节点 " + node.id + " 有多条出边，但没有任何 join 把 fork_id 声明为它；请创建配对 join。",
          { nodeId: node.id }
        ));
      }
    });
    if (problems.length > 0) {
      return problems;
    }
    const conditionEdges = [];
    nodes.forEach((node, position) => {
      if (node.type !== "condition") {
        return;
      }
      const config = configOf(node);
      const allowed = conditionPorts(config);
      const allowedSet = new Set(allowed);
      const actual = /* @__PURE__ */ new Set();
      edges.forEach((edge, edgePosition) => {
        if (edge.source !== node.id) {
          return;
        }
        const port = typeof edge.port === "string" && edge.port !== "" ? edge.port : "default";
        actual.add(port);
        if (!allowedSet.has(port)) {
          problems.push(problem(
            "bad_port",
            "/edges/" + edgePosition,
            "边 " + node.id + "→" + edge.target + ' 的端口 "' + port + '" 与 cases/default_port 不一致；可用端口：' + (allowed.length ? allowed.join("、") : "（无）") + "。",
            { nodeId: node.id, edgeIndex: edgePosition }
          ));
        }
      });
      for (const port of sortedStrings(allowedSet)) {
        if (!actual.has(port)) {
          problems.push(problem(
            "bad_port",
            "/nodes/" + position + "/config/cases",
            "条件节点 " + node.id + ' 的端口 "' + port + '" 没有对应出边；每个 case 端口和 default_port 都必须连线。',
            { nodeId: node.id }
          ));
        }
      }
    });
    edges.forEach((edge, edgePosition) => {
      const sourceNode = nodes[index.get(edge.source)];
      if (!sourceNode || sourceNode.type === "condition") {
        return;
      }
      if (Object.prototype.hasOwnProperty.call(edge, "port") && edge.port !== "default") {
        problems.push(problem(
          "bad_port",
          "/edges/" + edgePosition,
          "只有条件节点的出边可以设置 port；" + edge.source + " 的这条出边应删除端口。",
          { nodeId: edge.source, edgeIndex: edgePosition }
        ));
      }
    });
    if (problems.length > 0) {
      return problems;
    }
    const regions = nodes.filter((node) => node.type === "join").map((node) => [configOf(node).fork_id, node.id]).sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
    for (const [forkId, joinId] of regions) {
      const inside = /* @__PURE__ */ new Set();
      for (const nodeId of index.keys()) {
        if (nodeId === forkId || nodeId === joinId) {
          continue;
        }
        if (graph.dom.get(nodeId).has(forkId) && graph.pdom.get(nodeId).has(joinId)) {
          inside.add(nodeId);
        }
      }
      edges.forEach((edge, edgePosition) => {
        const sourceInside = inside.has(edge.source);
        const targetInside = inside.has(edge.target);
        let illegal = false;
        if (sourceInside && !targetInside && edge.target !== joinId) {
          illegal = true;
        } else if (targetInside && !sourceInside && edge.source !== forkId) {
          illegal = true;
        } else if (edge.source === forkId && edge.target !== joinId && !targetInside) {
          illegal = true;
        } else if (edge.target === joinId && edge.source !== forkId && !sourceInside) {
          illegal = true;
        }
        if (illegal) {
          problems.push(problem(
            "cross_region_edge",
            "/edges/" + edgePosition,
            "边 " + edge.source + "→" + edge.target + " 跨越了 region 边界：它必须连到配对 join（" + joinId + "），或在 join 前汇合；region 的入口是 " + forkId + "。",
            { nodeId: edge.source, edgeIndex: edgePosition }
          ));
        }
      });
    }
    if (problems.length > 0) {
      return problems;
    }
    nodes.forEach((node, position) => {
      if (node.type !== "join") {
        return;
      }
      const config = configOf(node);
      const required = Array.isArray(config.required_sources) ? config.required_sources : [];
      const optional = Array.isArray(config.optional_sources) ? config.optional_sources : [];
      const declared = [...required, ...optional];
      const actual = sortedStrings(new Set(
        edges.filter((edge) => edge.target === node.id).map((edge) => edge.source)
      ));
      const uniqueDeclared = new Set(declared);
      const overlap = required.some((id2) => optional.includes(id2));
      if (declared.length !== uniqueDeclared.size || overlap || !sameList(sortedStrings(uniqueDeclared), actual)) {
        problems.push(problem(
          "bad_join_sources",
          "/nodes/" + position + "/config/required_sources",
          "join " + node.id + " 的 required_sources/optional_sources 必须恰好覆盖所有直接前驱且互斥；当前声明 " + JSON.stringify(declared) + "，直接前驱是 " + JSON.stringify(actual) + "。",
          { nodeId: node.id }
        ));
      }
    });
    return problems;
  }
  function nextNodeId(nodes, type) {
    const used = new Set((nodes || []).map((node) => node.id));
    let counter = (nodes || []).length + 1;
    let id2 = type + counter;
    while (used.has(id2)) {
      counter += 1;
      id2 = type + counter;
    }
    return id2;
  }
  function addNode(graphNodes, graphEdges, type, layout) {
    const nodes = graphNodes || [];
    if (!NODE_TYPES.includes(type)) {
      return { ok: false, code: "unknown_type", reason: "画布不支持节点类型 " + type + "。" };
    }
    if (!REPEATABLE_NODE_TYPES.includes(type) && nodes.some((node) => typeOfNode(node) === type)) {
      return {
        ok: false,
        code: "node_exists",
        reason: "画布只允许一个" + (NODE_LABELS[type] || type) + "节点。"
      };
    }
    const id2 = type === "start" || type === "end" ? type : nextNodeId(nodes, type);
    const sameType = nodes.filter((node) => typeOfNode(node) === type).length;
    const position = DEFAULT_POSITIONS[type] ? { ...DEFAULT_POSITIONS[type], y: DEFAULT_POSITIONS[type].y + 120 * sameType } : { x: 280 + 240 * nodes.length, y: 320 };
    return {
      ok: true,
      node: {
        id: id2,
        type: "default",
        label: NODE_LABELS[type],
        class: "orchara-node orchara-node-" + type,
        position,
        data: {
          label: NODE_LABELS[type],
          nodeType: type,
          config: clone(defaultNode(type, id2).config),
          ...type === "condition" ? { ports: conditionPorts(defaultNode(type, id2).config) } : {}
        }
      },
      layout
    };
  }
  function removeNode(graphNodes, graphEdges, nodeId) {
    return {
      nodes: (graphNodes || []).filter((node) => node.id !== nodeId),
      edges: (graphEdges || []).filter((edge) => edge.source !== nodeId && edge.target !== nodeId)
    };
  }

  // src/lib/sources.js
  function escapePointer(token) {
    return String(token).replace(/~/g, "~0").replace(/\//g, "~1");
  }
  function unescapePointer(token) {
    return String(token).replace(/~1/g, "/").replace(/~0/g, "~");
  }
  function schemaProperties(schema, baseRef, basePath, out) {
    if (!schema || typeof schema !== "object") {
      return out;
    }
    const properties = schema.properties;
    if (!properties || typeof properties !== "object") {
      return out;
    }
    for (const name of Object.keys(properties).sort()) {
      const child = properties[name];
      const ref2 = baseRef + "/" + escapePointer(name);
      const path = basePath ? basePath + "." + name : name;
      out.push({
        ref: ref2,
        path,
        label: path,
        type: describeType(child)
      });
      if (child && child.type === "object") {
        schemaProperties(child, ref2, path, out);
      }
    }
    return out;
  }
  function describeType(schema) {
    if (!schema || typeof schema !== "object") {
      return "unknown";
    }
    const type = schema.type;
    if (Array.isArray(type)) {
      return type.join(" | ");
    }
    if (type === "array" && schema.items && schema.items.type) {
      return "array<" + schema.items.type + ">";
    }
    return type || "unknown";
  }
  function incomingEdges(definition) {
    const incoming = /* @__PURE__ */ new Map();
    for (const edge of definition.edges || []) {
      if (!incoming.has(edge.target)) {
        incoming.set(edge.target, []);
      }
      incoming.get(edge.target).push(edge.source);
    }
    return incoming;
  }
  function upstreamNodeIds(definition, nodeId) {
    const incoming = incomingEdges(definition);
    const order = [];
    const seen = /* @__PURE__ */ new Set([nodeId]);
    const pending = [...incoming.get(nodeId) || []];
    while (pending.length > 0) {
      const current = pending.shift();
      if (seen.has(current)) {
        continue;
      }
      seen.add(current);
      order.push(current);
      for (const parent of incoming.get(current) || []) {
        pending.push(parent);
      }
    }
    const position = new Map((definition.nodes || []).map((node, index) => [node.id, index]));
    order.sort((a, b) => (position.get(a) ?? 0) - (position.get(b) ?? 0));
    return order;
  }
  function joinEnvelopeSources(definition, nodeId) {
    const entries = [];
    const nodes = definition.nodes || [];
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const ancestors = new Set(upstreamNodeIds(definition, nodeId));
    const dom = dominanceOf(definition);
    for (const pair of regionPairs(definition)) {
      if (!ancestors.has(pair.joinId) || !byId.has(pair.joinId)) {
        continue;
      }
      if (dom !== null && !(dom.get(nodeId) || /* @__PURE__ */ new Set()).has(pair.joinId)) {
        continue;
      }
      const joinLabel = "join " + pair.joinId;
      entries.push({
        ref: "node:" + pair.joinId + "#/results",
        path: "results",
        label: pair.joinId + "#/results（汇合封套）",
        type: "object",
        source: joinLabel,
        sourceNodeId: pair.joinId
      });
      const predecessors = [...new Set(
        (definition.edges || []).filter((edge) => edge.target === pair.joinId).map((edge) => edge.source)
      )];
      for (const sourceId of predecessors) {
        const sourceNode = byId.get(sourceId);
        const sourceType2 = sourceNode ? sourceNode.type : "node";
        const valueType = describeType(sourceNode && sourceNode.output_schema);
        entries.push({
          ref: "node:" + pair.joinId + "#/results/" + escapePointer(sourceId),
          path: "results." + sourceId,
          label: pair.joinId + "#/results/" + sourceId + "（" + sourceType2 + " 结果封套）",
          type: "object",
          source: joinLabel,
          sourceNodeId: pair.joinId
        });
        entries.push({
          ref: "node:" + pair.joinId + "#/results/" + escapePointer(sourceId) + "/value",
          path: "results." + sourceId + ".value",
          label: pair.joinId + "#/results/" + sourceId + "/value（" + sourceType2 + "）",
          type: valueType,
          source: joinLabel,
          sourceNodeId: pair.joinId
        });
      }
    }
    return entries;
  }
  function legalSources(definition, nodeId) {
    const sources = [];
    if (!definition || !Array.isArray(definition.nodes)) {
      return sources;
    }
    const node = definition.nodes.find((item) => item.id === nodeId);
    if (!node || node.type === "start") {
      return sources;
    }
    for (const entry of schemaProperties(definition.input_schema, "input#", "", [])) {
      sources.push({ ...entry, source: "application input", sourceNodeId: "" });
    }
    const branch = branchNodeIds(definition);
    const dom = dominanceOf(definition);
    for (const upstreamId of upstreamNodeIds(definition, nodeId)) {
      const upstream = definition.nodes.find((item) => item.id === upstreamId);
      if (!upstream) {
        continue;
      }
      if (branch.has(upstreamId)) {
        continue;
      }
      if (dom !== null && !(dom.get(nodeId) || /* @__PURE__ */ new Set()).has(upstreamId)) {
        continue;
      }
      for (const entry of schemaProperties(upstream.output_schema, "node:" + upstreamId + "#", "", [])) {
        sources.push({
          ...entry,
          source: "node " + upstreamId + " (" + upstream.type + ")",
          sourceNodeId: upstreamId
        });
      }
    }
    for (const entry of joinEnvelopeSources(definition, nodeId)) {
      sources.push(entry);
    }
    return dedupe(sources);
  }
  function dedupe(entries) {
    const seen = /* @__PURE__ */ new Set();
    const result = [];
    for (const entry of entries) {
      if (seen.has(entry.ref)) {
        continue;
      }
      seen.add(entry.ref);
      result.push(entry);
    }
    return result;
  }
  function sourceType(definition, ref2) {
    if (typeof ref2 !== "string") {
      return "unknown";
    }
    const hash = ref2.indexOf("#");
    if (hash < 0) {
      return "unknown";
    }
    const head = ref2.slice(0, hash);
    const tokens = ref2.slice(hash + 1).split("/").filter((part) => part !== "").map(unescapePointer);
    if (head.startsWith("node:")) {
      const node = (definition.nodes || []).find((item) => item.id === head.slice(5));
      if (node && node.type === "join" && tokens.length > 0 && tokens[0] === "results") {
        if (tokens.length === 1) {
          return "object";
        }
        if (tokens.length === 2) {
          return "object";
        }
        if (tokens.length === 3 && tokens[2] === "value") {
          const source = (definition.nodes || []).find((item) => item.id === tokens[1]);
          return describeType(source && source.output_schema);
        }
        return "unknown";
      }
    }
    let schema;
    if (head === "input") {
      schema = definition.input_schema;
    } else if (head.startsWith("node:")) {
      const node = (definition.nodes || []).find((item) => item.id === head.slice(5));
      schema = node ? node.output_schema : null;
    }
    for (const token of tokens) {
      if (!schema || typeof schema !== "object") {
        return "unknown";
      }
      const properties = schema.properties;
      if (properties && Object.prototype.hasOwnProperty.call(properties, token)) {
        schema = properties[token];
        continue;
      }
      if (schema.type === "array" && /^\d+$/.test(token) && schema.items) {
        schema = schema.items;
        continue;
      }
      return "unknown";
    }
    return describeType(schema);
  }
  function sourceGroups(definition, nodeId) {
    const sources = legalSources(definition, nodeId);
    const groups = /* @__PURE__ */ new Map();
    for (const entry of sources) {
      const key = entry.sourceNodeId ? entry.sourceNodeId : "input";
      if (!groups.has(key)) {
        groups.set(key, { key, label: entry.source, options: [] });
      }
      groups.get(key).options.push(entry);
    }
    return [...groups.values()];
  }

  // src/lib/errors.js
  function nodeIndexFromPointer(pointer2) {
    const match = /^\/nodes\/(\d+)(?:\/|$)/.exec(String(pointer2 || ""));
    return match ? Number(match[1]) : null;
  }
  function edgeIndexFromPointer(pointer2) {
    const match = /^\/edges\/(\d+)(?:\/|$)/.exec(String(pointer2 || ""));
    return match ? Number(match[1]) : null;
  }
  function nodeIdForError(definition, error) {
    const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
    if (error && error.node_id) {
      return nodes.some((node) => node.id === error.node_id) ? error.node_id : "";
    }
    const index = nodeIndexFromPointer(error && error.pointer);
    if (index !== null && index >= 0 && index < nodes.length) {
      return nodes[index].id;
    }
    const edgeIndex = edgeIndexFromPointer(error && error.pointer);
    const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
    if (edgeIndex !== null && edgeIndex >= 0 && edgeIndex < edges.length) {
      return edges[edgeIndex].source || "";
    }
    return "";
  }
  function fieldForError(pointer2) {
    const text = String(pointer2 || "");
    const index = nodeIndexFromPointer(text);
    if (index === null) {
      const parts2 = text.split("/").filter((part) => part !== "");
      return parts2.length > 0 ? unescapePointer(parts2[0]) : "document";
    }
    const parts = text.split("/").filter((part) => part !== "").slice(2).map(unescapePointer);
    if (parts.length === 0) {
      return "node";
    }
    if (parts[0] === "inputs") {
      return parts.length > 1 ? "inputs." + parts[1] : "inputs";
    }
    if (parts[0] === "config") {
      return parts.length > 1 ? "config." + parts[1] : "config";
    }
    if (parts[0] === "output_schema") {
      return parts.length > 1 ? "output_schema." + parts.slice(1).join(".") : "output_schema";
    }
    return parts.join(".");
  }
  function fixHintForCode(code) {
    switch (String(code || "")) {
      case "cross_region_edge":
        return "这条连接跨越了 region 边界；请连到配对 join 或删除这条边。";
      case "invalid_region":
        return "条件节点与 join 必须双向配对（condition.join_id ↔ join.fork_id）；请检查配对面板。";
      case "bad_port":
        return "端口与 cases/default_port 不一致；请让每条出边的端口都出现在条件端口列表里。";
      case "bad_join_sources":
        return "join 来源必须覆盖所有直接前驱且 required/optional 互斥；请勾选完整。";
      case "merge_without_join":
        return "多个上游分支不能直接合并；请先创建配对 join 再连入。";
      case "fork_requires_join":
        return "多出口需要唯一配对 join 声明 fork；请创建 join 并把 fork_id 指向该节点。";
      case "port_exists":
        return "同一个条件端口只能连接一条出边；请换一个端口。";
      case "no_free_port":
        return "条件节点的端口都已连线；请新增 case/default_port 或删除一条出边。";
      case "missing_value":
        return "这个值在某些执行路径上可能没有被赋值；请改读 join 后按来源区分的结果。";
      case "start_end_count":
        return "画布必须恰好有一个开始节点和一个结束节点。";
      case "cycle":
        return "连接形成了环；工作流必须是有向无环图，请删除回边。";
      case "unreachable_node":
        return "该节点无法从开始节点到达；请补上入口连接。";
      case "cannot_reach_end":
        return "该节点无法到达结束节点；请补上通往 join/结束节点的连接。";
      case "dependency_not_ready":
        return "引用的工具/模型/子应用版本未就绪；请选择 ready 且已批准的固定版本。";
      case "duplicate_id":
        return "节点 id 重复；请删除重复节点或重新添加。";
      case "unknown_endpoint":
        return "连接引用了不存在的节点；请删除这条边并重新连接。";
      case "non_ancestor_ref":
        return "只能引用当前节点上游的值；请改用合法来源。";
      case "unknown_ref":
        return "引用的节点或字段不存在；请重新选择合法来源。";
      case "bad_pointer":
        return "引用的字段不在上游输出 Schema 中；请检查指针或补全输出 Schema。";
      case "type_mismatch":
        return "比较值的类型与输入不一致；条件比较不做类型强转。";
      default:
        return "请按服务端给出的 pointer 定位节点/边并修正配置；画布不会代替服务端校验。";
    }
  }
  function mapValidationErrors(definition, errors) {
    const list = Array.isArray(errors) ? errors : [];
    const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
    const edges = definition && Array.isArray(definition.edges) ? definition.edges : [];
    return list.map((error, index) => {
      const pointer2 = error && error.pointer ? error.pointer : "";
      const edgeIndex = edgeIndexFromPointer(pointer2);
      const edge = edgeIndex !== null && edgeIndex >= 0 && edgeIndex < edges.length ? edges[edgeIndex] : null;
      const nodeId = nodeIdForError(definition, error);
      let field = fieldForError(pointer2);
      if (edge) {
        const sourceNode = nodes.find((node) => node.id === edge.source);
        field = sourceNode && sourceNode.type === "condition" ? "edge.port" : "edge";
      }
      return {
        index,
        nodeId,
        field,
        pointer: pointer2,
        code: error && error.code || "validation_failed",
        message: error && error.message || "服务端拒绝了这个定义",
        hint: fixHintForCode(error && error.code),
        edgeIndex: edge ? edgeIndex : null,
        edgeId: edge ? edgeIdFor(edge, edgeIndex) : "",
        source: edge ? edge.source : "",
        target: edge ? edge.target : "",
        documentLevel: nodeId === "" && edge === null
      };
    });
  }

  // src/lib/export.js
  var SENSITIVE_KEY = /(secret|token|password|passwd|api[_-]?key|credential|bearer|oauth|memory|personal|run[_-]?input|endpoint)/i;
  var CONFIG_ALLOWLIST = {
    start: [],
    python: ["tool_version"],
    agent: [
      "model_profile_version",
      "instructions",
      "tool_versions",
      "subapp_versions",
      "max_turns"
    ],
    // F04B/T086: the response schema is node.output_schema, never a config key.
    model: ["model_profile_version", "prompt_template", "response_mode"],
    condition: ["join_id", "default_port", "cases"],
    join: ["mode", "fork_id", "required_sources", "optional_sources"],
    // T087: an approval node carries exactly the title and the expiry window; the
    // proposal is a bound input (definition.inputs), not a config field.
    approval: ["title", "expires_seconds"],
    end: []
  };
  var TOP_LEVEL_KEYS = ["dsl_version", "input_schema", "output_schema", "nodes", "edges", "policy"];
  var NODE_KEYS = ["id", "type", "inputs", "output_schema", "config"];
  var EDGE_KEYS = ["source", "target", "port"];
  var CASE_KEYS = ["input", "op", "value", "port"];
  var ExportRefused = class extends Error {
    constructor(code, path, message) {
      super(message);
      this.name = "ExportRefused";
      this.code = code;
      this.path = path;
    }
  };
  function walk(value, path, visit) {
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, path + "/" + index, visit));
      return;
    }
    if (value && typeof value === "object") {
      for (const key of Object.keys(value)) {
        visit(key, path + "/" + key);
        walk(value[key], path + "/" + key, visit);
      }
    }
  }
  function assertExportSafe(definition, layout) {
    walk(definition, "#/definition", (key, path) => {
      if (SENSITIVE_KEY.test(key)) {
        throw new ExportRefused(
          "sensitive_key",
          path,
          "导出被拒绝：definition 含有凭据/端点/运行输入/记忆类字段 " + JSON.stringify(key) + "。"
        );
      }
    });
    walk(layout, "#/layout", (key, path) => {
      if (SENSITIVE_KEY.test(key)) {
        throw new ExportRefused(
          "sensitive_key",
          path,
          "导出被拒绝：layout 含有凭据/端点/运行输入/记忆类字段 " + JSON.stringify(key) + "。"
        );
      }
    });
    return true;
  }
  function clone2(value) {
    return JSON.parse(JSON.stringify(value));
  }
  function sanitizeCase(item, path, dropped) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return item;
    }
    const cleaned = {};
    for (const key of Object.keys(item)) {
      if (CASE_KEYS.includes(key)) {
        cleaned[key] = item[key];
      } else {
        dropped.push({ path: path + "/" + escapePointer(key), reason: "unknown_config_key" });
      }
    }
    return cleaned;
  }
  function sanitizeDefinition(definition) {
    const dropped = [];
    const source = definition && typeof definition === "object" && !Array.isArray(definition) ? definition : {};
    const cleaned = {};
    for (const key of Object.keys(source)) {
      if (TOP_LEVEL_KEYS.includes(key)) {
        cleaned[key] = source[key];
      } else {
        dropped.push({ path: "/" + escapePointer(key), reason: "unknown_field" });
      }
    }
    const sourceNodes = Array.isArray(source.nodes) ? source.nodes : [];
    cleaned.nodes = sourceNodes.map((node, index) => {
      const target = {};
      const entry = node && typeof node === "object" && !Array.isArray(node) ? node : {};
      for (const key of Object.keys(entry)) {
        if (NODE_KEYS.includes(key)) {
          target[key] = entry[key];
        } else {
          dropped.push({ path: "/nodes/" + index + "/" + escapePointer(key), reason: "unknown_field" });
        }
      }
      const type = typeof entry.type === "string" ? entry.type : "";
      const allow = CONFIG_ALLOWLIST[type];
      const config = entry.config && typeof entry.config === "object" && !Array.isArray(entry.config) ? entry.config : {};
      const cleanConfig = {};
      for (const key of Object.keys(config).sort()) {
        if (!allow) {
          dropped.push({
            path: "/nodes/" + index + "/config/" + escapePointer(key),
            reason: "unknown_node_type"
          });
          continue;
        }
        if (!allow.includes(key)) {
          dropped.push({
            path: "/nodes/" + index + "/config/" + escapePointer(key),
            reason: "unknown_config_key"
          });
          continue;
        }
        cleanConfig[key] = config[key];
      }
      if (allow && type === "condition" && Array.isArray(cleanConfig.cases)) {
        cleanConfig.cases = cleanConfig.cases.map((item, position) => sanitizeCase(item, "/nodes/" + index + "/config/cases/" + position, dropped));
      }
      if (Object.prototype.hasOwnProperty.call(entry, "config")) {
        target.config = cleanConfig;
      }
      return target;
    });
    const sourceEdges = Array.isArray(source.edges) ? source.edges : [];
    cleaned.edges = sourceEdges.map((edge, index) => {
      const entry = edge && typeof edge === "object" && !Array.isArray(edge) ? edge : {};
      const target = {};
      for (const key of Object.keys(entry)) {
        if (EDGE_KEYS.includes(key)) {
          target[key] = entry[key];
        } else {
          dropped.push({ path: "/edges/" + index + "/" + escapePointer(key), reason: "unknown_field" });
        }
      }
      if (!Object.prototype.hasOwnProperty.call(target, "port")) {
        delete target.port;
      }
      return target;
    });
    return { definition: cleaned, dropped };
  }
  function exportPayload(definition, layout) {
    assertExportSafe(definition, layout);
    const sanitized = sanitizeDefinition(definition);
    const payload = {
      definition: sanitized.definition,
      layout: clone2(layout && typeof layout === "object" ? layout : {})
    };
    if (sanitized.dropped.length > 0) {
      payload.dropped = sanitized.dropped;
    }
    return payload;
  }
  function exportFilename(applicationId) {
    return "orchara-canvas-" + (applicationId || "draft") + ".json";
  }

  // src/main.js
  var SCHEMA_TYPES = ["string", "integer", "number", "boolean", "object", "array"];
  function clone3(value) {
    return JSON.parse(JSON.stringify(value));
  }
  function applicationIdFromPath(pathname) {
    const match = /^\/apps\/([0-9a-fA-F-]{36})\/edit\/?$/.exec(String(pathname || ""));
    return match ? match[1] : "";
  }
  function fieldElement(root2, field) {
    const escaped = String(field).replace(/["\\]/g, "\\$&");
    return root2.querySelector('[data-field="' + escaped + '"]');
  }
  function emptySchemaWithId() {
    return { type: "object", properties: {} };
  }
  function normalizeSchema(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return emptySchema();
    }
    if (raw.type !== "object") {
      return { type: "object", properties: {}, ...raw };
    }
    return raw;
  }
  function setSchemaProperty(schema, name, type, required) {
    const next = clone3(schema || emptySchemaWithId());
    next.type = "object";
    next.properties = next.properties && typeof next.properties === "object" ? next.properties : {};
    next.properties[name] = { ...next.properties[name] || {}, type };
    const requiredList = new Set(Array.isArray(next.required) ? next.required : []);
    if (required) {
      requiredList.add(name);
    } else {
      requiredList.delete(name);
    }
    next.required = [...requiredList].sort();
    return next;
  }
  function removeSchemaProperty(schema, name) {
    const next = clone3(schema || emptySchemaWithId());
    if (next.properties) {
      delete next.properties[name];
    }
    if (Array.isArray(next.required)) {
      next.required = next.required.filter((item) => item !== name);
    }
    return next;
  }
  function renderSchemaRows(definition, root2, state, schema, options) {
    const properties = schema.properties && typeof schema.properties === "object" ? schema.properties : {};
    const required = new Set(Array.isArray(schema.required) ? schema.required : []);
    const rows = Object.keys(properties).sort().map(
      (name) => h("div", { class: "schema-row", key: "schema-" + name }, [
        h("input", {
          type: "text",
          value: name,
          "data-field": "schema." + name + ".name",
          onInput: (event) => options.onRename(name, event.target.value)
        }),
        h("select", {
          value: properties[name].type || "string",
          "data-field": "schema." + name + ".type",
          onChange: (event) => options.onType(name, event.target.value)
        }, SCHEMA_TYPES.map((type) => h("option", { value: type }, type))),
        h("label", { class: "inline" }, [
          h("input", {
            type: "checkbox",
            checked: required.has(name),
            "data-field": "schema." + name + ".required",
            onChange: (event) => options.onRequired(name, event.target.checked)
          }),
          " required"
        ]),
        h("button", {
          type: "button",
          class: "link danger",
          "data-field": "schema." + name + ".remove",
          onClick: () => options.onRemove(name)
        }, "删除")
      ])
    );
    const draftName = state.schemaDrafts[options.draftKey] || "";
    return [
      ...rows.length ? rows : [h("p", { class: "hint" }, "尚未声明任何字段。")],
      h("div", { class: "schema-row add" }, [
        h("input", {
          type: "text",
          placeholder: "新字段名",
          value: draftName,
          "data-field": options.draftKey + ".new",
          onInput: (event) => {
            state.schemaDrafts[options.draftKey] = event.target.value;
          }
        }),
        h("button", {
          type: "button",
          onClick: () => {
            const name = String(state.schemaDrafts[options.draftKey] || "").trim();
            if (!name) {
              state.status = "字段名不能为空。";
              state.statusKind = "bad";
              return;
            }
            state.schemaDrafts[options.draftKey] = "";
            options.onAdd(name);
          }
        }, "添加字段")
      ])
    ];
  }
  function renderSourceSelect(definition, nodeId, value, onChange, fieldName) {
    const groups = sourceGroups(definition, nodeId);
    const options = [h("option", { value: "" }, "— 选择合法来源 —")];
    for (const group of groups) {
      const children2 = group.options.map(
        (entry) => h(
          "option",
          { value: entry.ref },
          entry.label + " (" + entry.type + ") — " + group.label
        )
      );
      options.push(h("optgroup", { label: group.label }, children2));
    }
    if (value && !groups.some((group) => group.options.some((entry) => entry.ref === value))) {
      options.push(h("option", { value, disabled: true }, value + "（不再合法）"));
    }
    return h("select", {
      value: value || "",
      "data-field": fieldName,
      onChange: (event) => onChange(event.target.value)
    }, options);
  }
  function renderMappingEditor(definition, state, node) {
    const inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};
    const names = Object.keys(inputs).sort();
    const draftKey = node.id + ".inputName";
    return [
      h("h3", {}, "输入映射"),
      h(
        "p",
        { class: "hint" },
        "只提供应用输入属性与合法上游节点的输出；分支节点在 join 前只能通过 node:<join>#/results/<source> 读取，自身和下游不会出现在列表里。"
      ),
      ...names.length === 0 ? [h("p", { class: "hint" }, "还没有映射。工具/Agent 的输入名需要与节点输入名一致。")] : names.map((name) => h("div", { class: "mapping-row", key: "map-" + name }, [
        h("code", { class: "mapping-name" }, name),
        renderSourceSelect(
          definition,
          node.id,
          inputs[name] && inputs[name].ref,
          (ref2) => {
            const next = clone3(node.inputs || {});
            if (ref2) {
              next[name] = { ref: ref2 };
            } else {
              delete next[name];
            }
            node.inputs = next;
            commit(state);
          },
          "inputs." + name
        ),
        h(
          "span",
          { class: "type-chip" },
          inputs[name] && inputs[name].ref ? sourceType(definition, inputs[name].ref) : "literal"
        ),
        h("button", {
          type: "button",
          class: "link danger",
          "data-field": "inputs." + name + ".remove",
          onClick: () => {
            const next = clone3(node.inputs || {});
            delete next[name];
            node.inputs = next;
            commit(state);
          }
        }, "删除")
      ])),
      h("div", { class: "schema-row add" }, [
        h("input", {
          type: "text",
          placeholder: "新输入名",
          value: state.inputDrafts[draftKey] || "",
          "data-field": "inputs.new",
          onInput: (event) => {
            state.inputDrafts[draftKey] = event.target.value;
          }
        }),
        h("button", {
          type: "button",
          onClick: () => {
            const name = String(state.inputDrafts[draftKey] || "").trim();
            if (!name) {
              state.status = "输入名不能为空。";
              state.statusKind = "bad";
              return;
            }
            state.inputDrafts[draftKey] = "";
            node.inputs = { ...node.inputs || {}, [name]: { ref: "" } };
            commit(state);
          }
        }, "添加输入")
      ])
    ];
  }
  function renderOutputSchemaEditor(definition, state, node, title) {
    const text = JSON.stringify(node.output_schema || emptySchemaWithId(), null, 2);
    return [
      h("h3", {}, title || "输出 Schema"),
      h("textarea", {
        class: "schema-json",
        spellcheck: "false",
        "data-field": "output_schema",
        value: state.schemaText[node.id] !== void 0 ? state.schemaText[node.id] : text,
        onInput: (event) => {
          const value = event.target.value;
          state.schemaText[node.id] = value;
          try {
            const parsed = JSON.parse(value);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
              throw new Error("必须是 JSON 对象");
            }
            node.output_schema = parsed;
            state.schemaErrors[node.id] = "";
            commit(state);
          } catch (error) {
            state.schemaErrors[node.id] = "输出 Schema 不是合法 JSON 对象：" + error.message;
          }
        }
      }),
      state.schemaErrors[node.id] ? h("p", { class: "error-text", "data-field": "output_schema.error" }, state.schemaErrors[node.id]) : h("p", { class: "hint" }, "输出 Schema 是对象类型，属性名就是下游可引用的字段。")
    ];
  }
  function renderStartPanel(definition, state, node) {
    const schema = definition.input_schema || emptySchemaWithId();
    return [
      h("h3", {}, "应用输入 Schema"),
      h("p", { class: "hint" }, "开始节点没有输入；这里声明应用运行时可以提供的输入字段。"),
      ...renderSchemaRows(definition, state.root, state, schema, {
        draftKey: "input_schema",
        onAdd: (name) => {
          definition.input_schema = setSchemaProperty(definition.input_schema, name, "string", false);
          commit(state);
        },
        onRename: (name, next) => {
          const trimmed = String(next || "").trim();
          if (!trimmed || trimmed === name) {
            return;
          }
          const nextSchema = clone3(definition.input_schema || emptySchemaWithId());
          nextSchema.properties = nextSchema.properties || {};
          nextSchema.properties[trimmed] = nextSchema.properties[name];
          delete nextSchema.properties[name];
          nextSchema.required = (nextSchema.required || []).map((item) => item === name ? trimmed : item);
          definition.input_schema = nextSchema;
          commit(state);
        },
        onType: (name, type) => {
          definition.input_schema = setSchemaProperty(
            definition.input_schema,
            name,
            type,
            (definition.input_schema.required || []).includes(name)
          );
          commit(state);
        },
        onRequired: (name, required) => {
          definition.input_schema = setSchemaProperty(
            definition.input_schema,
            name,
            definition.input_schema.properties[name].type || "string",
            required
          );
          commit(state);
        },
        onRemove: (name) => {
          definition.input_schema = removeSchemaProperty(definition.input_schema, name);
          commit(state);
        }
      })
    ];
  }
  function renderEndPanel(definition, state, node) {
    const schema = definition.output_schema || emptySchemaWithId();
    const properties = schema.properties && typeof schema.properties === "object" ? schema.properties : {};
    return [
      h("h3", {}, "应用输出声明"),
      h("p", { class: "hint" }, "结束节点把上游字段映射成应用输出；声明的必填输出必须全部映射。"),
      ...renderSchemaRows(definition, state.root, state, schema, {
        draftKey: "output_schema",
        onAdd: (name) => {
          definition.output_schema = setSchemaProperty(definition.output_schema, name, "string", false);
          commit(state);
        },
        onRename: (name, next) => {
          const trimmed = String(next || "").trim();
          if (!trimmed || trimmed === name) {
            return;
          }
          const nextSchema = clone3(definition.output_schema || emptySchemaWithId());
          nextSchema.properties[trimmed] = nextSchema.properties[name];
          delete nextSchema.properties[name];
          nextSchema.required = (nextSchema.required || []).map((item) => item === name ? trimmed : item);
          definition.output_schema = nextSchema;
          commit(state);
        },
        onType: (name, type) => {
          definition.output_schema = setSchemaProperty(
            definition.output_schema,
            name,
            type,
            (definition.output_schema.required || []).includes(name)
          );
          commit(state);
        },
        onRequired: (name, required) => {
          definition.output_schema = setSchemaProperty(
            definition.output_schema,
            name,
            definition.output_schema.properties[name].type || "string",
            required
          );
          commit(state);
        },
        onRemove: (name) => {
          definition.output_schema = removeSchemaProperty(definition.output_schema, name);
          commit(state);
        }
      }),
      h("h3", {}, "输出映射（来自合法上游字段）"),
      ...Object.keys(properties).sort().map((name) => h("div", { class: "mapping-row", key: "out-" + name }, [
        h("code", { class: "mapping-name" }, name),
        renderSourceSelect(
          definition,
          node.id,
          node.inputs[name] && node.inputs[name].ref,
          (ref2) => {
            const next = clone3(node.inputs || {});
            if (ref2) {
              next[name] = { ref: ref2 };
            } else {
              delete next[name];
            }
            node.inputs = next;
            commit(state);
          },
          "inputs." + name
        ),
        h(
          "span",
          { class: "type-chip" },
          node.inputs[name] && node.inputs[name].ref ? sourceType(definition, node.inputs[name].ref) : "未映射"
        )
      ]))
    ];
  }
  function renderPythonPanel(definition, state, node) {
    const tools = state.tools.filter((tool2) => tool2.state === "ready" && tool2.approved === true);
    const selected = node.config.tool_version || "";
    const tool = tools.find((item) => item.id === selected);
    return [
      h("h3", {}, "Python 工具版本"),
      h("p", { class: "hint" }, "只列出 ready 且已批准的工具版本（GET /api/v1/tools）。"),
      h("select", {
        "data-field": "config.tool_version",
        value: selected,
        onChange: (event) => {
          node.config = { ...node.config, tool_version: event.target.value };
          const picked = tools.find((item) => item.id === event.target.value);
          if (picked && picked.output_schema && typeof picked.output_schema === "object" && !Array.isArray(picked.output_schema)) {
            node.output_schema = normalizeSchema(picked.output_schema);
          }
          commit(state);
        }
      }, [
        h("option", { value: "" }, state.catalogsState === "loading" ? "正在加载工具…" : "— 选择工具 —"),
        ...tools.map((item) => h(
          "option",
          { value: item.id },
          item.id + (item.state ? " (" + item.state + ")" : "")
        ))
      ]),
      state.catalogsError ? h("p", { class: "error-text" }, state.catalogsError) : null,
      tool ? h("div", { class: "schema-preview" }, [
        h("h4", {}, "工具输入 Schema（只读）"),
        h("pre", {}, JSON.stringify(tool.input_schema || {}, null, 2)),
        h("h4", {}, "工具输出 Schema（只读）"),
        h("pre", {}, JSON.stringify(tool.output_schema || {}, null, 2))
      ]) : null,
      ...renderMappingEditor(definition, state, node),
      ...renderOutputSchemaEditor(definition, state, node)
    ];
  }
  function renderAgentPanel(definition, state, node) {
    const models = state.models.filter((model) => model.validation_state === "passed");
    const tools = state.tools.filter((tool) => tool.state === "ready" && tool.approved === true);
    const selectedTools = Array.isArray(node.config.tool_versions) ? node.config.tool_versions : [];
    return [
      h("h3", {}, "模型 Profile"),
      h("p", { class: "hint" }, "只列出 validation_state=passed 的模型（GET /api/v1/models）。"),
      h("select", {
        "data-field": "config.model_profile_version",
        value: node.config.model_profile_version || "",
        onChange: (event) => {
          node.config = { ...node.config, model_profile_version: event.target.value };
          commit(state);
        }
      }, [
        h("option", { value: "" }, state.catalogsState === "loading" ? "正在加载模型…" : "— 选择模型 —"),
        ...models.map((model) => h(
          "option",
          { value: model.id },
          (model.name || model.id) + " [" + (model.validation_state || "unknown") + "]"
        ))
      ]),
      h("label", { class: "field-label" }, "instructions"),
      h("textarea", {
        "data-field": "config.instructions",
        value: node.config.instructions || "",
        onInput: (event) => {
          node.config = { ...node.config, instructions: event.target.value };
          commit(state);
        }
      }),
      h("label", { class: "field-label" }, "max_turns"),
      h("input", {
        type: "number",
        min: 1,
        max: 50,
        "data-field": "config.max_turns",
        value: node.config.max_turns === void 0 ? 8 : node.config.max_turns,
        onInput: (event) => {
          node.config = { ...node.config, max_turns: Number(event.target.value) };
          commit(state);
        }
      }),
      h("label", { class: "field-label" }, "允许的工具版本（ready+approved）"),
      h("select", {
        multiple: true,
        size: Math.min(5, Math.max(2, tools.length)),
        "data-field": "config.tool_versions",
        onChange: (event) => {
          const values = Array.from(event.target.selectedOptions).map((option) => option.value);
          node.config = { ...node.config, tool_versions: values };
          commit(state);
        }
      }, tools.map((tool) => h("option", {
        value: tool.id,
        selected: selectedTools.includes(tool.id)
      }, tool.id))),
      h("p", { class: "hint" }, "subapp_versions 固定为空数组（本票不提供子应用调用入口）。"),
      ...renderMappingEditor(definition, state, node),
      ...renderOutputSchemaEditor(definition, state, node)
    ];
  }
  function renderModelPanel(definition, state, node) {
    const models = state.models.filter((model) => model.validation_state === "passed");
    const config = node.config && typeof node.config === "object" ? node.config : {};
    const template = typeof config.prompt_template === "string" ? config.prompt_template : "";
    const boundNames = Object.keys(node.inputs && typeof node.inputs === "object" ? node.inputs : {}).sort();
    const insertVariable = (name) => {
      const current = typeof node.config.prompt_template === "string" ? node.config.prompt_template : "";
      node.config = { ...node.config, prompt_template: current + "{{" + name + "}}" };
      commit(state);
    };
    return [
      h("h3", {}, "模型 Profile（固定版本，不接受 latest）"),
      h("p", { class: "hint" }, "只列出 validation_state=passed 的模型（GET /api/v1/models）。"),
      h("select", {
        "data-field": "config.model_profile_version",
        value: config.model_profile_version || "",
        onChange: (event) => {
          node.config = { ...node.config, model_profile_version: event.target.value };
          commit(state);
        }
      }, [
        h("option", { value: "" }, state.catalogsState === "loading" ? "正在加载模型…" : "— 选择模型 —"),
        ...models.map((model) => h(
          "option",
          { value: model.id },
          (model.name || model.id) + " [" + (model.validation_state || "unknown") + "]"
        ))
      ]),
      state.catalogsError ? h("p", { class: "error-text" }, state.catalogsError) : null,
      h("label", { class: "field-label" }, "prompt_template（只允许 {{name}} 取已绑定输入）"),
      h("textarea", {
        "data-field": "config.prompt_template",
        value: template,
        onInput: (event) => {
          node.config = { ...node.config, prompt_template: event.target.value };
          commit(state);
        }
      }),
      h("div", { class: "variable-inserts" }, [
        h("span", { class: "hint" }, "插入变量："),
        ...boundNames.map((name) => h("button", {
          type: "button",
          class: "chip",
          "data-insert-variable": name,
          onClick: () => insertVariable(name)
        }, "{{" + name + "}}")),
        boundNames.length === 0 ? h("span", { class: "hint" }, "先在下方输入映射里绑定输入，模板才能引用 {{name}}。") : null
      ]),
      h(
        "p",
        { class: "hint" },
        "模板只做 {{name}} 替换：属性链/表达式/未绑定变量会被执行器拒绝为 node_prompt_invalid；对象与数组按 JSON 渲染。"
      ),
      h("label", { class: "field-label" }, "response_mode"),
      h("select", {
        "data-field": "config.response_mode",
        value: config.response_mode || "json",
        onChange: (event) => {
          node.config = { ...node.config, response_mode: event.target.value };
          commit(state);
        }
      }, MODEL_RESPONSE_MODES.map((mode) => h("option", { value: mode }, mode))),
      // The response schema IS the node's output_schema; it is not a config key.
      ...renderOutputSchemaEditor(definition, state, node, "响应 Schema（= node.output_schema）"),
      ...renderMappingEditor(definition, state, node)
    ];
  }
  function caseValueText(value) {
    if (value === void 0) {
      return "";
    }
    try {
      return JSON.stringify(value);
    } catch (error) {
      return "";
    }
  }
  function updateCase(state, node, index, patch) {
    const cases = clone3(Array.isArray(node.config.cases) ? node.config.cases : []);
    cases[index] = { ...cases[index] || {}, ...patch };
    if (patch.op === "exists" && Object.prototype.hasOwnProperty.call(cases[index], "value")) {
      delete cases[index].value;
    }
    node.config = { ...node.config, cases };
    commit(state);
  }
  function renderConditionPanel(definition, state, node) {
    const config = node.config || {};
    const cases = Array.isArray(config.cases) ? config.cases : [];
    const joins = definition.nodes.filter((item) => item.type === "join");
    const ports = conditionPorts(config);
    const connected = new Set(
      state.flowEdges.filter((edge) => edge.source === node.id).map((edge) => edgePortOf(edge)).filter((value) => value !== "")
    );
    const inputOptions = Object.keys(node.inputs || {}).sort();
    const rows = cases.map((item, index) => {
      const draftKey = node.id + ".caseValue." + index;
      const valueText = state.caseValueDrafts[draftKey] !== void 0 ? state.caseValueDrafts[draftKey] : caseValueText(item.value);
      return h("div", { class: "case-row", key: "case-" + index, "data-case-index": index }, [
        h("span", { class: "case-index" }, String(index + 1)),
        h("input", {
          type: "text",
          placeholder: "输入名",
          list: "condition-input-list-" + node.id,
          value: item.input || "",
          "data-field": "config.cases." + index + ".input",
          onInput: (event) => updateCase(state, node, index, { input: event.target.value })
        }),
        h("select", {
          "data-field": "config.cases." + index + ".op",
          value: item.op || "eq",
          onChange: (event) => updateCase(state, node, index, { op: event.target.value })
        }, CONDITION_OPS.map((op) => h("option", { value: op }, op))),
        h("input", {
          type: "text",
          placeholder: item.op === "exists" ? "（exists 不需要 value）" : 'JSON 值（如 true / "x" / 3）',
          value: valueText,
          disabled: item.op === "exists",
          "data-field": "config.cases." + index + ".value",
          onInput: (event) => {
            const text = event.target.value;
            state.caseValueDrafts[draftKey] = text;
            if (text.trim() === "") {
              const casesNext = clone3(node.config.cases || []);
              delete casesNext[index].value;
              node.config = { ...node.config, cases: casesNext };
              commit(state);
              return;
            }
            try {
              const parsed = JSON.parse(text);
              delete state.caseValueDrafts[draftKey];
              updateCase(state, node, index, { value: parsed });
            } catch (error) {
              state.status = "case " + (index + 1) + " 的 value 不是合法 JSON：" + error.message;
              state.statusKind = "bad";
            }
          }
        }),
        h("input", {
          type: "text",
          placeholder: "端口",
          value: item.port || "",
          "data-field": "config.cases." + index + ".port",
          onInput: (event) => updateCase(state, node, index, { port: event.target.value })
        }),
        h("button", {
          type: "button",
          class: "link",
          "data-field": "config.cases." + index + ".up",
          disabled: index === 0,
          onClick: () => {
            const next = clone3(node.config.cases || []);
            [next[index - 1], next[index]] = [next[index], next[index - 1]];
            node.config = { ...node.config, cases: next };
            commit(state);
          }
        }, "↑"),
        h("button", {
          type: "button",
          class: "link",
          "data-field": "config.cases." + index + ".down",
          disabled: index === cases.length - 1,
          onClick: () => {
            const next = clone3(node.config.cases || []);
            [next[index + 1], next[index]] = [next[index], next[index + 1]];
            node.config = { ...node.config, cases: next };
            commit(state);
          }
        }, "↓"),
        h("button", {
          type: "button",
          class: "link danger",
          "data-field": "config.cases." + index + ".remove",
          onClick: () => {
            const next = clone3(node.config.cases || []);
            next.splice(index, 1);
            node.config = { ...node.config, cases: next };
            commit(state);
          }
        }, "删除")
      ]);
    });
    return [
      h("h3", {}, "条件分支"),
      h(
        "p",
        { class: "hint" },
        "cases 按顺序求值，首个匹配决定唯一出口；其余分支整段 skipped。端口必须与出边一致，每个出边都要带 port。"
      ),
      h(
        "datalist",
        { id: "condition-input-list-" + node.id },
        inputOptions.map((name) => h("option", { value: name }))
      ),
      h("label", { class: "field-label" }, "配对 join（join_id）"),
      h("select", {
        "data-field": "config.join_id",
        value: config.join_id || "",
        onChange: (event) => {
          const joinId = event.target.value;
          node.config = { ...node.config, join_id: joinId };
          const target = definition.nodes.find((item) => item.id === joinId && item.type === "join");
          if (target) {
            target.config = { ...target.config, fork_id: node.id };
          }
          commit(state);
        }
      }, [
        h("option", { value: "" }, joins.length ? "— 选择配对 join —" : "尚未创建 join 节点"),
        ...joins.map((join) => h(
          "option",
          { value: join.id },
          join.id + (join.config && join.config.fork_id ? "（fork: " + join.config.fork_id + "）" : "")
        ))
      ]),
      h("label", { class: "field-label" }, "default_port"),
      h("input", {
        type: "text",
        value: config.default_port || "",
        "data-field": "config.default_port",
        onInput: (event) => {
          node.config = { ...node.config, default_port: event.target.value };
          commit(state);
        }
      }),
      h("h3", {}, "cases（顺序即优先级）"),
      ...rows.length ? rows : [h("p", { class: "hint" }, "至少需要一个 case（契约要求 minItems=1）。")],
      h("div", { class: "schema-row add" }, [
        h("button", {
          type: "button",
          "data-field": "config.cases.add",
          onClick: () => {
            const next = clone3(node.config.cases || []);
            next.push({ input: "", op: "eq", value: true, port: "port" + (next.length + 1) });
            node.config = { ...node.config, cases: next };
            commit(state);
          }
        }, "添加 case")
      ]),
      h("h4", {}, "端口预览"),
      h(
        "div",
        { class: "port-preview", "data-field": "config.ports" },
        ports.length ? ports.map((port) => h("span", {
          class: "port-chip" + (connected.has(port) ? " connected" : ""),
          "data-port": port
        }, port + (connected.has(port) ? " ✓ 已连线" : "（未连线）"))) : [h("span", { class: "hint" }, "还没有可用端口：请填写 case port 或 default_port。")]
      ),
      ...renderMappingEditor(definition, state, node),
      ...renderOutputSchemaEditor(definition, state, node)
    ];
  }
  function renderJoinPanel(definition, state, node) {
    const config = node.config || {};
    const outDegree = /* @__PURE__ */ new Map();
    for (const edge of definition.edges || []) {
      outDegree.set(edge.source, (outDegree.get(edge.source) || 0) + 1);
    }
    const forkCandidates = definition.nodes.filter((item) => item.id !== node.id && item.type !== "end" && item.type !== "join" && (outDegree.get(item.id) || 0) >= 1).sort((a, b) => (outDegree.get(b.id) || 0) - (outDegree.get(a.id) || 0));
    const predecessors = [...new Set(
      (definition.edges || []).filter((edge) => edge.target === node.id).map((edge) => edge.source)
    )].sort();
    const predecessorLabels = new Map(definition.nodes.map((item) => [item.id, item.type]));
    const required = Array.isArray(config.required_sources) ? config.required_sources : [];
    const optional = Array.isArray(config.optional_sources) ? config.optional_sources : [];
    const declared = [...required, ...optional];
    const missing = predecessors.filter((id2) => !declared.includes(id2));
    const extra = declared.filter((id2) => !predecessors.includes(id2));
    const selectFor = (field, selected, other, onChange) => h("select", {
      multiple: true,
      size: Math.min(6, Math.max(2, predecessors.length)),
      "data-field": field,
      onChange: (event) => {
        onChange(Array.from(event.target.selectedOptions).map((option) => option.value));
      }
    }, predecessors.map((id2) => h("option", {
      value: id2,
      selected: selected.includes(id2)
    }, id2 + " (" + (predecessorLabels.get(id2) || "?") + ")" + (other.includes(id2) ? " — 已在一侧" : ""))));
    return [
      h("h3", {}, "并行汇合（join）"),
      h(
        "p",
        { class: "hint" },
        "join 只等待已激活的分支；required 分支失败会 fail-fast，optional 仅允许纯读/计算分支。"
      ),
      h("label", { class: "field-label" }, "mode"),
      h("input", {
        type: "text",
        value: config.mode || "all_active",
        readonly: true,
        "data-field": "config.mode"
      }),
      h("p", { class: "hint" }, "（契约只有 all_active 一种模式）"),
      h("label", { class: "field-label" }, "fork_id（配对分叉节点）"),
      h("select", {
        "data-field": "config.fork_id",
        value: config.fork_id || "",
        onChange: (event) => {
          const forkId = event.target.value;
          node.config = { ...node.config, fork_id: forkId };
          const fork = definition.nodes.find((item) => item.id === forkId);
          if (fork && fork.type === "condition") {
            fork.config = { ...fork.config, join_id: node.id };
          }
          commit(state);
        }
      }, [
        h(
          "option",
          { value: "" },
          forkCandidates.length ? "— 选择分叉节点 —" : "画布上还没有可分叉的节点（先连一条出边）"
        ),
        ...forkCandidates.map((fork) => h(
          "option",
          { value: fork.id },
          fork.id + " (" + fork.type + ")" + (fork.config && fork.config.join_id ? "（paired join: " + fork.config.join_id + "）" : "")
        ))
      ]),
      h("h4", {}, "required_sources（必需分支）"),
      selectFor("config.required_sources", required, optional, (values) => {
        node.config = {
          ...node.config,
          required_sources: values,
          optional_sources: optional.filter((id2) => !values.includes(id2))
        };
        commit(state);
      }),
      h("h4", {}, "optional_sources（可选分支）"),
      selectFor("config.optional_sources", optional, required, (values) => {
        node.config = {
          ...node.config,
          optional_sources: values,
          required_sources: required.filter((id2) => !values.includes(id2))
        };
        commit(state);
      }),
      h("p", {
        class: "hint",
        "data-field": "config.join_sources.coverage"
      }, "直接前驱：" + (predecessors.length ? predecessors.join("、") : "（无）") + "；未覆盖：" + (missing.length ? missing.join("、") : "无") + "；多余：" + (extra.length ? extra.join("、") : "无"))
    ];
  }
  function renderApprovalPanel(definition, state, node) {
    const config = node.config || {};
    const inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};
    const binding = inputs.proposal && typeof inputs.proposal === "object" ? inputs.proposal : {};
    const boundRef = typeof binding.ref === "string" ? binding.ref : "";
    const literal = binding.literal !== void 0 ? JSON.stringify(binding.literal, null, 2) : "";
    const draftKey = node.id + ".proposalLiteral";
    const literalValue = Object.prototype.hasOwnProperty.call(state.proposalDrafts, draftKey) ? state.proposalDrafts[draftKey] : literal;
    return [
      h("h3", {}, "人工审批"),
      h(
        "p",
        { class: "hint", "data-field": "approval.boundary" },
        "审批只代表业务决定，不授权后续外部写：恢复后继续本次运行，但后面的写工具仍需各自的 operation 审批（服务端能力代理逐次校验）。"
      ),
      h("label", { class: "field-label" }, "标题 title（必填，最多 200 字符）"),
      h("input", {
        type: "text",
        maxlength: 200,
        "data-field": "config.title",
        value: config.title || "",
        onInput: (event) => {
          const value = String(event.target.value || "").trim();
          node.config = { ...node.config, title: value };
          commit(state);
        }
      }),
      h("p", { class: "hint" }, "标题为空时发布校验会拒绝该节点。"),
      h("label", { class: "field-label" }, "有效期 expires_seconds（60..86400 秒）"),
      h("input", {
        type: "number",
        min: 60,
        max: 86400,
        "data-field": "config.expires_seconds",
        value: config.expires_seconds === void 0 ? 3600 : config.expires_seconds,
        onInput: (event) => {
          node.config = { ...node.config, expires_seconds: Number(event.target.value) };
          commit(state);
        }
      }),
      h("h3", {}, "审批提议（proposal 输入，JSON 对象）"),
      h(
        "p",
        { class: "hint" },
        "提议由使用者审批时原样展示；可以绑定一个合法上游对象，或直接编写字面量对象。"
      ),
      h("label", { class: "field-label" }, "来源绑定（proposal ref）"),
      renderSourceSelect(
        definition,
        node.id,
        boundRef,
        (ref2) => {
          const next = clone3(node.inputs || {});
          if (ref2) {
            next.proposal = { ref: ref2 };
          } else if (!Object.prototype.hasOwnProperty.call(next, "proposal") || next.proposal.literal === void 0) {
            next.proposal = { literal: {} };
          }
          node.inputs = next;
          delete state.proposalDrafts[draftKey];
          commit(state);
        },
        "inputs.proposal.ref"
      ),
      h("label", { class: "field-label" }, boundRef ? "字面量（设置会清除来源绑定）" : "字面量 proposal（JSON 对象）"),
      h("textarea", {
        rows: 6,
        "data-field": "inputs.proposal.literal",
        value: literalValue,
        onInput: (event) => {
          const text = String(event.target.value || "");
          state.proposalDrafts[draftKey] = text;
          let parsed;
          try {
            parsed = JSON.parse(text);
          } catch (error) {
            state.status = "proposal 字面量不是合法 JSON：" + (error && error.message ? error.message : "解析失败");
            state.statusKind = "bad";
            return;
          }
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            state.status = "proposal 字面量必须是 JSON 对象（不是数组或标量）。";
            state.statusKind = "bad";
            return;
          }
          node.inputs = { ...node.inputs || {}, proposal: { literal: parsed } };
          state.status = "已更新 proposal 字面量。";
          state.statusKind = "ok";
          refreshLocalProblems(state);
          emitChange(state);
        }
      }),
      h(
        "p",
        { class: "hint", "data-field": "inputs.proposal.kind" },
        boundRef ? "当前为来源绑定：" + boundRef : binding.literal !== void 0 ? "当前为字面量对象。" : "尚未设置 proposal 输入。"
      )
    ];
  }
  function renderEdgePanel(state, edge, index) {
    const definition = state.definition;
    const sourceNode = definition.nodes.find((node) => node.id === edge.source);
    const targetNode = definition.nodes.find((node) => node.id === edge.target);
    if (!sourceNode) {
      return [h("p", { class: "hint" }, "这条连接引用了不存在的节点。")];
    }
    const isCondition = sourceNode.type === "condition";
    const allowed = conditionPorts(sourceNode.config || {});
    const current = edgePortOf(edge);
    return [
      h("h3", {}, "连接 · " + edge.source + " → " + edge.target),
      h(
        "p",
        { class: "hint" },
        (targetNode ? "目标类型 " + targetNode.type + "；" : "") + (isCondition ? "条件出边必须带端口。" : "非条件出边没有端口。")
      ),
      isCondition ? h("label", { class: "field-label" }, "port") : null,
      isCondition ? h("input", {
        type: "text",
        value: current,
        "data-field": "edge.port",
        onInput: (event) => {
          const port = event.target.value;
          const next = {
            ...edge,
            label: port,
            sourceHandle: port || void 0,
            data: { ...edge.data || {}, port },
            class: "orchara-edge" + (port ? " orchara-edge-port" : "")
          };
          state.flowEdges = state.flowEdges.map((item) => item.id === edge.id ? next : item);
          rebuild(state);
          if (port && !allowed.includes(port)) {
            state.status = '端口 "' + port + '" 不在该条件节点的 cases/default_port 中；可用：' + (allowed.length ? allowed.join("、") : "（无）");
            state.statusKind = "bad";
          } else {
            state.status = "已更新边 " + edge.source + " → " + edge.target + ' 的端口为 "' + port + '"。';
            state.statusKind = "ok";
          }
        }
      }) : h("p", { class: "hint" }, "端口只对条件节点的出边生效。"),
      isCondition ? h(
        "p",
        { class: "hint", "data-field": "edge.ports" },
        "可用端口：" + (allowed.length ? allowed.join("、") : "（无）")
      ) : null,
      h("button", {
        type: "button",
        class: "link danger",
        "data-field": "edge.remove",
        onClick: () => {
          state.flowEdges = state.flowEdges.filter((item) => item.id !== edge.id);
          state.selectedEdgeId = "";
          rebuild(state);
          state.status = "已删除连接 " + edge.source + " → " + edge.target + "。";
          state.statusKind = "ok";
        }
      }, "删除这条连接"),
      h("p", { class: "hint" }, "边 #" + index + "（definition.edges[" + index + "]）")
    ];
  }
  function refreshLocalProblems(state) {
    state.localProblems = regionProblems(state.definition);
    return state.localProblems;
  }
  function renderErrorList(state) {
    const serverErrors = state.errors;
    if (serverErrors.length) {
      return h(
        "div",
        { class: "errors", id: "canvas-errors" },
        serverErrors.map(
          (error) => h("button", {
            type: "button",
            class: "error-item" + (error.edgeIndex !== null ? " error-edge" : ""),
            key: "error-" + error.index,
            "data-node-id": error.nodeId,
            "data-code": error.code,
            "data-edge-id": error.edgeId || "",
            "data-field": "error." + error.index,
            onClick: () => focusError(state, error)
          }, [
            h("strong", {}, "[" + error.code + "] "),
            error.message + " ",
            error.source && error.target ? h("code", {}, error.source + "→" + error.target + " ") : null,
            h("code", {}, error.pointer || error.field),
            h("span", { class: "error-hint" }, " 修复：" + error.hint),
            error.nodeId ? h("span", { class: "hint" }, " · 定位到 " + error.nodeId) : null
          ])
        )
      );
    }
    if (state.localProblems.length) {
      return h(
        "div",
        { class: "errors", id: "canvas-local-problems" },
        state.localProblems.map(
          (problem2, index) => h("button", {
            type: "button",
            class: "error-item" + (problem2.edgeIndex !== null ? " error-edge" : ""),
            key: "local-" + index,
            "data-node-id": problem2.nodeId,
            "data-code": problem2.code,
            "data-field": "local." + index,
            onClick: () => focusLocalProblem(state, problem2)
          }, [
            h("strong", {}, "[本地提示 " + problem2.code + "] "),
            problem2.message + " ",
            h("code", {}, problem2.pointer || ""),
            problem2.nodeId ? h("span", { class: "hint" }, " · 定位到 " + problem2.nodeId) : null
          ])
        )
      );
    }
    return h(
      "div",
      { class: "errors ok", "data-field": "errors" },
      state.manifestHash ? ["校验通过。服务端 manifest SHA256: ", h("code", { id: "canvas-manifest-hash" }, state.manifestHash)] : "尚未发现定位错误。"
    );
  }
  function focusError(state, error) {
    if (error.edgeIndex !== null) {
      const edge = state.flowEdges[error.edgeIndex] || null;
      state.highlightedEdgeId = error.edgeId || (edge ? edge.id : "");
      state.selectedEdgeId = state.highlightedEdgeId;
      state.selectedId = "";
      state.status = "[" + error.code + "] " + (error.source && error.target ? error.source + "→" + error.target + "：" : "") + error.message;
      state.statusKind = "bad";
      scrollCanvasIntoView(state);
      return;
    }
    state.highlightedEdgeId = "";
    state.selectedId = error.nodeId;
    state.selectedEdgeId = "";
    state.status = "[" + error.code + "] " + error.message;
    state.statusKind = "bad";
    nextTick(() => {
      const element = fieldElement(state.root, error.field);
      if (element && typeof element.focus === "function") {
        element.focus();
        scrollCanvasIntoView(state);
      }
    });
  }
  function focusLocalProblem(state, problem2) {
    focusError(state, {
      code: problem2.code,
      message: problem2.message,
      hint: "",
      field: problem2.code === "bad_port" || problem2.code === "cross_region_edge" ? "edge.port" : "config",
      pointer: problem2.pointer,
      nodeId: problem2.nodeId,
      edgeIndex: problem2.edgeIndex,
      edgeId: problem2.edgeIndex !== null && state.flowEdges[problem2.edgeIndex] ? state.flowEdges[problem2.edgeIndex].id : "",
      source: "",
      target: ""
    });
  }
  function scrollCanvasIntoView(state) {
    const stage = state.root && state.root.querySelector ? state.root.querySelector(".canvas-stage") : null;
    if (stage && typeof stage.scrollIntoView === "function") {
      stage.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  async function validate(state) {
    state.status = "正在请求服务端校验…";
    state.statusKind = "pending";
    state.manifestHash = "";
    const globalApi = typeof window !== "undefined" ? window.apiFull : void 0;
    const globalCSRF = typeof window !== "undefined" ? window.csrf : void 0;
    if (typeof globalApi !== "function" || state.applicationId === "") {
      refreshLocalProblems(state);
      state.status = "页面 API 助手不可用，无法请求服务端校验；已显示本地规则提示。";
      state.statusKind = "bad";
      return;
    }
    try {
      const token = typeof globalCSRF === "function" ? await globalCSRF() : null;
      const result = await globalApi(
        "/api/v1/applications/" + encodeURIComponent(state.applicationId) + "/validate",
        { method: "POST", csrf: token }
      );
      state.errors = mapValidationErrors(state.definition, result.payload && result.payload.errors);
      if (result.payload && result.payload.valid === true) {
        state.errors = [];
        state.manifestHash = result.headers && typeof result.headers.get === "function" ? result.headers.get("X-Manifest-SHA256") || "" : "";
        refreshLocalProblems(state);
        state.status = "校验通过。" + (state.localProblems.length ? "（本地仍有 " + state.localProblems.length + " 条提示，以服务端为准。）" : state.manifestHash ? " manifest SHA256 已显示。" : "");
        state.statusKind = "ok";
        return;
      }
      state.status = "校验失败：" + state.errors.length + " 个定位错误。点击错误跳转到节点字段或高亮连接。";
      state.statusKind = "bad";
      if (state.errors.length > 0) {
        focusError(state, state.errors[0]);
      }
    } catch (error) {
      refreshLocalProblems(state);
      state.status = "校验请求失败 [" + (error.code || "error") + "] " + error.message;
      state.statusKind = "bad";
    }
  }
  function downloadExport(state) {
    try {
      const payload = exportPayload(state.definition, state.layout);
      const text = JSON.stringify(payload, null, 2);
      const link = document.createElement("a");
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      link.href = url;
      link.download = exportFilename(state.applicationId);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      const dropped = Array.isArray(payload.dropped) ? payload.dropped.length : 0;
      state.status = "已导出 {definition, layout}（不含凭据/端点/运行输入/记忆数据）" + (dropped ? "；按白名单丢弃 " + dropped + " 个未展示字段。" : "。");
      state.statusKind = dropped ? "pending" : "ok";
      state.lastExportDropped = payload.dropped || [];
    } catch (error) {
      state.status = "导出被拒绝 [" + (error.code || "error") + "] " + error.message;
      state.statusKind = "bad";
    }
  }
  async function loadCatalogs(state) {
    const globalApi = typeof window !== "undefined" ? window.apiFull : void 0;
    if (typeof globalApi !== "function") {
      state.catalogsState = "error";
      state.catalogsError = "页面 API 助手不可用，工具/模型选择器为空。";
      return;
    }
    try {
      const [tools, models] = await Promise.all([
        globalApi("/api/v1/tools?limit=100"),
        globalApi("/api/v1/models?limit=100")
      ]);
      state.tools = Array.isArray(tools.payload && tools.payload.items) ? tools.payload.items : [];
      state.models = Array.isArray(models.payload && models.payload.items) ? models.payload.items : [];
      state.catalogsState = "ready";
    } catch (error) {
      state.catalogsState = "error";
      state.catalogsError = "加载工具/模型目录失败 [" + (error.code || "error") + "] " + error.message;
    }
  }
  function syncGraphConfigs(state) {
    const byId = new Map(state.definition.nodes.map((node) => [node.id, node]));
    state.flowNodes = state.flowNodes.map((flowNode) => {
      const definitionNode = byId.get(flowNode.id);
      if (!definitionNode) {
        return flowNode;
      }
      const data = {
        ...flowNode.data,
        config: clone3(definitionNode.config || {})
      };
      if (definitionNode.type === "condition") {
        data.ports = conditionPorts(definitionNode.config || {});
      }
      if (definitionNode.type === "join") {
        data.sourceLabels = [...new Set(
          state.definition.edges.filter((edge) => edge.target === definitionNode.id).map((edge) => edge.source)
        )];
      }
      return { ...flowNode, data };
    });
  }
  function syncLayout(state) {
    state.layout = layoutFromGraph(state.flowNodes, state.layout);
    emitChange(state);
  }
  function rebuild(state) {
    state.definition = graphToDefinition(state.flowNodes, state.flowEdges, state.definition);
    state.layout = layoutFromGraph(state.flowNodes, state.layout);
    syncGraphConfigs(state);
    refreshLocalProblems(state);
    emitChange(state);
  }
  function emitChange(state) {
    if (typeof state.options.onChange === "function") {
      state.options.onChange(clone3(state.definition), clone3(state.layout));
    }
  }
  function commit(state) {
    syncGraphConfigs(state);
    refreshLocalProblems(state);
    emitChange(state);
  }
  function renderFlowNode(props) {
    const data = props && props.data || {};
    const type = data.nodeType || "python";
    const ports = type === "condition" ? Array.isArray(data.ports) && data.ports.length ? data.ports : conditionPorts(data.config || {}) : [];
    const handles = [];
    if (type !== "start") {
      handles.push(h(_sfc_main$f, {
        type: "target",
        position: Position.Left,
        id: "in",
        class: "orchara-handle orchara-handle-target"
      }));
    }
    if (type === "condition") {
      ports.forEach((port, index) => {
        const top = (index + 1) / (ports.length + 1) * 100;
        handles.push(h(_sfc_main$f, {
          type: "source",
          position: Position.Right,
          id: port,
          class: "orchara-handle orchara-handle-source",
          style: { top: top + "%" }
        }));
        handles.push(h("span", {
          class: "orchara-port-label",
          style: { top: top + "%" }
        }, port));
      });
    } else if (type !== "end") {
      handles.push(h(_sfc_main$f, {
        type: "source",
        position: Position.Right,
        id: "out",
        class: "orchara-handle orchara-handle-source"
      }));
    }
    const height = type === "condition" ? Math.max(48, ports.length * 26 + 28) : 48;
    const sourceLabels = type === "join" && Array.isArray(data.sourceLabels) ? data.sourceLabels : [];
    return h("div", {
      class: "orchara-flow-node orchara-node-" + type,
      style: { minHeight: height + "px" }
    }, [
      h("span", { class: "orchara-flow-label" }, data.label || type),
      h("span", { class: "orchara-flow-type" }, type),
      sourceLabels.length ? h("span", { class: "orchara-flow-sources" }, "来源: " + sourceLabels.join(" · ")) : null,
      ...handles
    ]);
  }
  function renderApp(state) {
    const nodes = state.flowNodes;
    const edges = state.highlightedEdgeId ? state.flowEdges.map((edge) => edge.id === state.highlightedEdgeId ? { ...edge, class: (edge.class || "orchara-edge") + " orchara-edge-invalid" } : edge) : state.flowEdges;
    const selected = state.definition.nodes.find((node) => node.id === state.selectedId);
    const selectedEdgeIndex = state.selectedEdgeId ? state.flowEdges.findIndex((edge) => edge.id === state.selectedEdgeId) : -1;
    const selectedEdge = selectedEdgeIndex >= 0 ? state.flowEdges[selectedEdgeIndex] : null;
    const invalidNodeIds = new Set(state.errors.filter((error) => error.nodeId).map((error) => error.nodeId));
    return h("div", { class: "orchara-canvas" }, [
      h("div", { class: "canvas-toolbar" }, [
        h(
          "div",
          { class: "palette", role: "group", "aria-label": "节点面板" },
          NODE_TYPES.map((type) => h("button", {
            type: "button",
            class: "palette-button",
            "data-node-type": type,
            disabled: !REPEATABLE_NODE_TYPES.includes(type) && state.definition.nodes.some((node) => node.type === type),
            onClick: () => {
              const result = addNode(nodes, edges, type, state.layout);
              if (!result.ok) {
                state.status = result.reason;
                state.statusKind = "bad";
                return;
              }
              state.flowNodes = [...nodes, result.node];
              rebuild(state);
              state.selectedId = result.node.id;
              state.selectedEdgeId = "";
              state.status = "已添加 " + NODE_LABELS[type] + "（" + result.node.id + "）。";
              state.statusKind = "ok";
            }
          }, "+ " + NODE_LABELS[type]))
        ),
        h("div", { class: "toolbar-actions" }, [
          h("button", {
            type: "button",
            id: "canvas-validate",
            class: "primary",
            onClick: () => validate(state)
          }, "校验草稿"),
          h("button", {
            type: "button",
            id: "canvas-export",
            onClick: () => downloadExport(state)
          }, "导出 {definition, layout}")
        ])
      ]),
      h("div", { class: "canvas-body" }, [
        h(
          "div",
          { class: "canvas-stage" },
          h(_sfc_main$1, {
            nodes,
            edges,
            fitViewOnInit: true,
            minZoom: 0.2,
            maxZoom: 2,
            "onNodesChange": (changes) => {
              state.flowNodes = applyNodeChanges(changes, state.flowNodes);
              syncLayout(state);
            },
            "onEdgesChange": (changes) => {
              state.flowEdges = applyEdgeChanges(changes, state.flowEdges);
              rebuild(state);
            },
            "onConnect": (connection) => {
              const verdict = canConnect(state.flowNodes, state.flowEdges, connection);
              if (!verdict.ok) {
                state.status = "拒绝连接：" + verdict.reason + " [" + verdict.code + "]";
                state.statusKind = "bad";
                return;
              }
              const port = verdict.port || "";
              const edge = {
                id: edgeIdFor(
                  { source: connection.source, target: connection.target },
                  state.flowEdges.length
                ),
                source: connection.source,
                target: connection.target,
                sourceHandle: port || connection.sourceHandle || void 0,
                targetHandle: "in",
                label: port,
                data: { port },
                class: "orchara-edge" + (port ? " orchara-edge-port" : "")
              };
              state.flowEdges = [...state.flowEdges, edge];
              rebuild(state);
              state.status = "已连接 " + connection.source + " → " + connection.target + (port ? "（端口 " + port + "）" : "") + "。";
              state.statusKind = "ok";
            },
            "onNodeClick": ({ node }) => {
              state.selectedId = node.id;
              state.selectedEdgeId = "";
              state.highlightedEdgeId = "";
            },
            "onEdgeClick": ({ edge }) => {
              state.selectedEdgeId = edge.id;
              state.selectedId = "";
            },
            "onNodeDragStop": () => syncLayout(state),
            "onPaneClick": () => {
              state.selectedId = "";
              state.selectedEdgeId = "";
            }
          }, {
            "node-default": (props) => renderFlowNode(props),
            default: () => [h(_sfc_main2), h(_sfc_main3)]
          })
        ),
        h("aside", { class: "config-panel", "aria-live": "polite" }, [
          h("div", { class: "panel-head" }, [
            h("h2", {}, selected ? (NODE_LABELS[selected.type] || selected.type) + " · " + selected.id : selectedEdge ? "连接 · " + selectedEdge.source + " → " + selectedEdge.target : "选择节点配置"),
            selected ? h("button", {
              type: "button",
              class: "link danger",
              id: "canvas-delete-node",
              onClick: () => {
                const removed = removeNode(nodes, edges, selected.id);
                state.flowNodes = removed.nodes;
                state.flowEdges = removed.edges;
                state.selectedId = "";
                rebuild(state);
                state.status = "已删除节点 " + selected.id + "；配对 join 的悬空来源已清理。";
                state.statusKind = "ok";
              }
            }, "删除节点") : null
          ]),
          !selected && !selectedEdge ? h(
            "p",
            { class: "hint" },
            "点击节点配置、点击连线编辑端口。条件节点按端口分出各分支，普通节点多出口前必须由 join 声明 fork；只有 join 可以多入。Delete 删除选中的节点或连线。"
          ) : h("div", { class: "panel-fields" }, [
            selected && selected.type === "start" ? renderStartPanel(state.definition, state, selected) : null,
            selected && selected.type === "python" ? renderPythonPanel(state.definition, state, selected) : null,
            selected && selected.type === "agent" ? renderAgentPanel(state.definition, state, selected) : null,
            selected && selected.type === "model" ? renderModelPanel(state.definition, state, selected) : null,
            selected && selected.type === "condition" ? renderConditionPanel(state.definition, state, selected) : null,
            selected && selected.type === "join" ? renderJoinPanel(state.definition, state, selected) : null,
            selected && selected.type === "approval" ? renderApprovalPanel(state.definition, state, selected) : null,
            selected && selected.type === "end" ? renderEndPanel(state.definition, state, selected) : null,
            selected && !NODE_TYPES.includes(selected.type) ? [h(
              "p",
              { class: "hint" },
              "该节点类型（" + selected.type + "）不在本画布的创作面板内；定义会原样保存，配置请用页面 JSON 视图编辑。"
            )] : null,
            selectedEdge ? renderEdgePanel(state, selectedEdge, selectedEdgeIndex) : null,
            selected ? [
              h("h3", {}, "布局"),
              h(
                "p",
                { class: "hint" },
                "位置保存在独立的 layout.positions，不进入执行定义，也不改变发布哈希。"
              )
            ] : null
          ]),
          h("div", {
            id: "canvas-status",
            class: "status " + (state.statusKind || ""),
            role: "status"
          }, state.status || "画布已就绪。"),
          renderErrorList(state)
        ])
      ])
    ]);
  }
  function initialState(root2, options) {
    const definition = options && options.definition && typeof options.definition === "object" ? clone3(options.definition) : defaultDefinition();
    if (!Array.isArray(definition.nodes) || definition.nodes.length === 0) {
      const fresh = defaultDefinition();
      definition.nodes = fresh.nodes;
      definition.edges = fresh.edges;
    }
    if (!definition.dsl_version) {
      definition.dsl_version = "1.0";
    }
    if (!definition.input_schema || typeof definition.input_schema !== "object") {
      definition.input_schema = emptySchemaWithId();
    }
    if (!definition.output_schema || typeof definition.output_schema !== "object") {
      definition.output_schema = emptySchemaWithId();
    }
    if (!definition.policy || typeof definition.policy !== "object") {
      definition.policy = defaultDefinition().policy;
    }
    const layout = options && options.layout && typeof options.layout === "object" && Object.keys(options.layout).length > 0 ? clone3(options.layout) : null;
    const effectiveLayout = layout && layout.positions ? layout : defaultLayout(definition);
    const graph = definitionToGraph(definition, effectiveLayout);
    return {
      root: root2,
      options: options || {},
      applicationId: options && options.applicationId || applicationIdFromPath(window.location.pathname),
      definition,
      layout: effectiveLayout,
      flowNodes: graph.nodes,
      flowEdges: graph.edges,
      selectedId: "start",
      selectedEdgeId: "",
      highlightedEdgeId: "",
      status: "画布已就绪。",
      statusKind: "",
      errors: [],
      localProblems: regionProblems(definition),
      manifestHash: "",
      tools: [],
      models: [],
      catalogsState: "loading",
      catalogsError: "",
      schemaDrafts: {},
      inputDrafts: {},
      schemaText: {},
      schemaErrors: {},
      caseValueDrafts: {},
      proposalDrafts: {},
      lastExportDropped: []
    };
  }
  function mount(root2, options) {
    if (!root2) {
      throw new Error("canvas mount requires a root element");
    }
    const state = reactive(initialState(root2, options));
    const app = createApp({ render: () => renderApp(state) });
    app.mount(root2);
    emitChange(state);
    loadCatalogs(state);
    const onKeydown = (event) => {
      const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
      if (["input", "textarea", "select"].includes(tag)) {
        return;
      }
      if (event.key === "Delete" || event.key === "Backspace") {
        if (state.selectedEdgeId) {
          state.flowEdges = state.flowEdges.filter((edge) => edge.id !== state.selectedEdgeId);
          state.selectedEdgeId = "";
          rebuild(state);
          event.preventDefault();
          return;
        }
        if (state.selectedId) {
          const removed = removeNode(state.flowNodes, state.flowEdges, state.selectedId);
          state.flowNodes = removed.nodes;
          state.flowEdges = removed.edges;
          state.selectedId = "";
          rebuild(state);
          event.preventDefault();
        }
      }
    };
    window.addEventListener("keydown", onKeydown);
    return {
      setDraft(definition, layout) {
        const next = initialState(root2, {
          ...options,
          definition,
          layout
        });
        state.definition = next.definition;
        state.layout = next.layout;
        state.flowNodes = next.flowNodes;
        state.flowEdges = next.flowEdges;
        state.selectedId = state.definition.nodes[0] ? state.definition.nodes[0].id : "";
        state.selectedEdgeId = "";
        state.highlightedEdgeId = "";
        state.errors = [];
        state.localProblems = next.localProblems;
        state.manifestHash = "";
        state.caseValueDrafts = {};
        state.schemaText = {};
        state.status = "已加载服务器草稿。";
        state.statusKind = "ok";
      },
      getDraft() {
        return { definition: clone3(state.definition), layout: clone3(state.layout) };
      },
      validate() {
        return validate(state);
      },
      exportDraft() {
        downloadExport(state);
      },
      regionProblems() {
        return regionProblems(state.definition);
      },
      destroy() {
        window.removeEventListener("keydown", onKeydown);
        app.unmount();
      }
    };
  }
  if (typeof window !== "undefined") {
    window.OrcharaCanvas = { mount };
  }
  return __toCommonJS(main_exports);
})();
