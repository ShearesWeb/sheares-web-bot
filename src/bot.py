import logging

from dotenv import load_dotenv
from telegram import Update

from telegram_app import build_application, get_bot_token

def main() -> None:
    load_dotenv()

    try:
        token = get_bot_token()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = build_application(token)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
