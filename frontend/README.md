# Music Auto Show frontend

This Vite SPA is the browser control surface for the Rust Music Auto Show service. It uses TanStack Router, Query, Form, and Table v9, Effect, Buf-generated protobuf definitions, Connect gRPC-Web, shadcn/ui, Tailwind CSS variables, and Credenza.

Run it against the Rust service on port 3000:

```bash
bun run dev
```

The dev server proxies the protobuf service path. Production assets are built by the root project and embedded in the Rust executable.

Do not edit `src/components/ui/**`. Add application composition outside that directory and use Credenza for modal flows.
