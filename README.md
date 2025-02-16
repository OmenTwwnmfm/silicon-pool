A local tool for managing SiliconFlow API keys. It supports batch import of API keys and balance checks, as well as request forwarding and load balancing.

Usage:

1. Install `uv`: https://docs.astral.sh/uv/getting-started/installation
2. Run `uv run main.py`

    > You can also use other package tool like `pip` since I also wrote a `requirements.txt`
3. Visit the management panel at http://127.0.0.1:7898 and fill your keys.
4. Set the OpenAI `BASE_URL` to `http://127.0.0.1:7898` and `API_KEY` to any value in the application you are using.
5. Enjoy!

**Keep the generated `pool.db` secret as it contains your API keys!**
