from pyrogram import Client
import asyncio

async def main():
    print("=" * 50)
    print("🔑 Telegram 会话生成器")
    print("=" * 50)
    
    api_id = int(input("请输入你的 API_ID: "))
    api_hash = input("请输入你的 API_HASH: ")
    
    print("\n📱 请用你的真实 Telegram 账号登录（不是机器人账号）")
    print("手机号格式: +66628409482\n")
    
    async with Client("session", api_id=api_id, api_hash=api_hash) as app:
        me = await app.get_me()
        print(f"\n✅ 登录成功！账号: {me.first_name} (@{me.username})")
        print("=" * 50)
        print("你的会话字符串（复制这整串）:\n")
        print(await app.export_session_string())
        print("=" * 50)
        print("⚠️  请将上面的字符串保存好，不要泄露给任何人！")

asyncio.run(main())