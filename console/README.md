# syros console frontend

React 19 + TypeScript + Vite + Tailwind CSS 4. Talks to the JSON API served by
`syros.console.server` (see `src/syros/console/api.py` for the endpoints).

```
npm install
npm run dev      # dev server with /api proxied to localhost:8484 (run `syros console --no-open` alongside)
npm run build    # type-check + emit ../src/syros/console/static/ — commit the output
```

The build output is committed so the Python package is self-contained: `pip install syros`
and the Docker image serve the console without a Node toolchain.
