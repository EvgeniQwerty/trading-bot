import asyncio
import json
import imaplib
import email
from typing import List, Dict
from telebot.async_telebot import AsyncTeleBot
import bitget.v2.spot.order_api as spotOrderApi
import bitget.v2.spot.account_api as spotAccountApi
import bitget.v2.spot.market_api as spotMarketApi
import creds
from dataclasses import dataclass
from bitget_functions import OrderManager, AssetManager, BillAnalyzer
import nest_asyncio

@dataclass
class TradingSettings:
    use_fix_deposit: bool
    fix_deposit: float

class ConfigManager:
    @staticmethod
    def read_json(name: str = "coins") -> Dict:
        with open(f"{name}.json", "r") as file:
            return json.load(file)

    @staticmethod
    def write_json(json_obj: Dict, name: str = "coins") -> None:
        with open(f"{name}.json", "w") as file:
            file.write(json.dumps(json_obj, indent=4))

class EmailMonitor:
    def __init__(self, imap_server: str, username: str, password: str):
        self.imap_server = imap_server
        self.username = username
        self.password = password

    async def get_trading_signals(self) -> List[str]:
        try:
            imap = imaplib.IMAP4_SSL(self.imap_server, 993)
            imap.login(self.username, self.password)
            imap.select()

            messages = []
            typ, message_numbers = imap.search(None, "UNSEEN")
            for num in message_numbers[0].split():
                typ, msg_data = imap.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                email_body = msg.get_payload(decode=True).decode()
                if "buy" in email_body or "sell" in email_body:
                    messages.append(email_body)
            imap.logout()
            return messages
        except Exception as e:
            print(f"Ошибка подключения к IMAP: {e}")
            return []

class TradingBot:
    def __init__(self):
        self.bot = AsyncTeleBot(creds.TELEGRAM_API_KEY)
        self.order_manager = OrderManager(
            spotOrderApi.OrderApi(
                creds.BITGET_API_KEY, creds.BITGET_SECRET_KEY, creds.BITGET_PASSPHRASE
            )
        )
        self.asset_manager = AssetManager(
            spotAccountApi.AccountApi(
                creds.BITGET_API_KEY, creds.BITGET_SECRET_KEY, creds.BITGET_PASSPHRASE
            ),
            spotMarketApi.MarketApi(
                creds.BITGET_API_KEY, creds.BITGET_SECRET_KEY, creds.BITGET_PASSPHRASE
            )
        )
        self.bill_analyzer = BillAnalyzer(
            spotAccountApi.AccountApi(
                creds.BITGET_API_KEY, creds.BITGET_SECRET_KEY, creds.BITGET_PASSPHRASE
            )
        )
        self.email_monitor = EmailMonitor(
            creds.IMAP_SERVER, creds.EMAIL_USERNAME, creds.EMAIL_PASSWORD
        )
        self.config = ConfigManager()

    async def setup_bot_commands(self):
        @self.bot.message_handler(commands=["start", "help"])
        async def send_welcome(message):
            help_text = (
                "ℹ️Доступные команды:\n"
                "/assets - вывести данные о монетах\n"
                "/ostat - полная статистика сделок за последние 10 дней\n"
                "/mstat - помесячная статистика\n"
            )
            await self.bot.reply_to(message, help_text)

        @self.bot.message_handler(commands=["assets"])
        async def send_assets(message):
            assets = self.asset_manager.get_all_assets()
            await self.bot.reply_to(message, self.asset_manager.format_assets_message(assets))

        @self.bot.message_handler(commands=["ostat"])
        async def send_order_statistics(message):
            """Отправляет статистику по сделкам за последние 10 дней"""
            try:
                stats, coins_in_trade = await self.bill_analyzer.process_bills(10)
                
                response = "📊 Статистика сделок за 10 дней:\n\n"
                
                if stats:
                    for stat in stats:
                        response += self.bill_analyzer.format_trade_statistics(stat)
                
                if coins_in_trade:
                    response += "\n📈 Активные сделки:\n"
                    for stat in coins_in_trade.values():
                        response += self.bill_analyzer.format_trade_statistics(stat)
                        
                if not stats and not coins_in_trade:
                    response += "Нет данных о сделках за указанный период"
                    
                await self.bot.reply_to(message, response)
            except Exception as e:
                error_message = f"❌ Ошибка при получении статистики: {str(e)}"
                await self.bot.reply_to(message, error_message)

        @self.bot.message_handler(commands=["mstat"])
        async def send_monthly_statistics(message):
            """Отправляет обобщённую месячную статистику"""
            try:
                stats, _ = await self.bill_analyzer.process_bills(30)
                
                if not stats:
                    await self.bot.reply_to(message, "Нет данных о сделках за последний месяц")
                    return
                    
                monthly_stats = self.bill_analyzer.format_monthly_statistics(stats)
                await self.bot.reply_to(message, monthly_stats)
            except Exception as e:
                error_message = f"❌ Ошибка при получении месячной статистики: {str(e)}"
                await self.bot.reply_to(message, error_message)

    async def trading_loop(self):
        while True:
            market_info = self.config.read_json()
            messages = await self.email_monitor.get_trading_signals()
            for msg in messages:
                await self.process_trading_signal(msg, market_info)
            await asyncio.sleep(300)

    async def start(self):
        await self.setup_bot_commands()
        loop_task = asyncio.create_task(self.trading_loop())
        polling_task = asyncio.create_task(self.bot.infinity_polling())
        await asyncio.gather(loop_task, polling_task)

nest_asyncio.apply()  # Патчинг для работы в Jupyter Notebook

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.start())