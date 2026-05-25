import os
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot Worker Online")

if __name__ == "__main__":
    app = web.Application()
    app.router.add_get('/', handle)
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
