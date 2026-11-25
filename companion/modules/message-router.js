// JARVIS Companion — Message Type Registry
// Maps message types to handler functions. Open for extension (OCP).

class MessageRouter {
  constructor() {
    this._handlers = new Map();
    this._binaryHandler = null;
  }

  register(type, handlerFn) {
    this._handlers.set(type, handlerFn);
  }

  route(msg, conn, pipeline) {
    const handler = this._handlers.get(msg.type);
    if (handler) return handler(msg, conn, pipeline);
  }

  setBinaryHandler(fn) {
    this._binaryHandler = fn;
  }

  routeBinary(buffer, conn, pipeline) {
    if (this._binaryHandler) return this._binaryHandler(buffer, conn, pipeline);
  }
}

module.exports = MessageRouter;
