import asyncio
import json
import imaplib
import email
from typing import List, Dict, Optional
from telebot.async_telebot import AsyncTeleBot
import bitget.v2.spot.order_api as spotOrderApi
import bitget.v2.spot.account_api as spotAccountApi
import bitget.v2.spot.market_api as spotMarketApi
import creds
from dataclasses import dataclass
from bitget_functions import OrderManager, AssetManager, BillAnalyzer
from datetime import datetime as dt
#import nest_asyncio

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
        self.signal_processor = TradingSignalProcessor(
            self.bot,
            self.order_manager,
            self.asset_manager,
            self.bill_analyzer,
            self.config
        )

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
                await self.signal_processor.process_trading_signal(msg, market_info)
            await asyncio.sleep(300)

    async def start(self):
        await self.setup_bot_commands()
        loop_task = asyncio.create_task(self.trading_loop())
        polling_task = asyncio.create_task(self.bot.infinity_polling())
        await asyncio.gather(loop_task, polling_task)

class TradingSignalProcessor:
    def __init__(self, bot, order_manager, asset_manager, bill_analyzer, config):
        self.bot = bot
        self.order_manager = order_manager
        self.asset_manager = asset_manager
        self.bill_analyzer = bill_analyzer
        self.config = config

    async def process_trading_signal(self, message: str, market_info: List[dict]) -> None:
        """Основной метод обработки торговых сигналов"""
        coin_info = self._find_coin_info(message, market_info)
        if not coin_info:
            return

        signal_parts = message.split(" @ ")
        if "sell" in message:
            await self._handle_sell_signal(coin_info, signal_parts, market_info)
        elif "buy" in message:
            await self._handle_buy_signal(coin_info, market_info)

    def _find_coin_info(self, message: str, market_info: List[dict]) -> Optional[dict]:
        """Поиск информации о монете из сигнала"""
        return next(
            (info for info in market_info if info["coin"] in message),
            None
        )

    async def _handle_sell_signal(self, coin_info: dict, signal_parts: List[str], market_info: List[dict]) -> None:
        if not coin_info["in_trade"]:
            return

        quantity = self._prepare_sell_quantity(coin_info)
        if not quantity:
            return

        success = self._execute_sell_order(coin_info, quantity, market_info)
        if success and len(signal_parts) == 3:
            await self._send_sell_notification(signal_parts[0])

    def _prepare_sell_quantity(self, coin_info: dict) -> Optional[float]:
        try:
            quantity = self.asset_manager.get_asset_quantity(coin_info["coin"])
            return round(float(quantity), coin_info["decimals"])
        except Exception as e:
            print(f"Ошибка при подготовке количества для продажи: {e}")
            return None

    def _execute_sell_order(self, coin_info: dict, quantity: float, market_info: List[dict]) -> bool:
        try:
            order_id = self.order_manager.sell(coin_info["coin"], quantity)
            if order_id:
                coin_info["in_trade"] = False
                self.config.write_json(market_info)
                return True
            return False
        except Exception as e:
            print(f"Ошибка при выполнении ордера на продажу: {e}")
            return False

    async def _send_sell_notification(self, coin_signal: str) -> None:
        """Отправка уведомления о продаже"""
        try:
            stats, _ = await self.bill_analyzer.process_bills(10)
            if stats:
                for stat in reversed(stats):
                    if coin_signal in stat["coin"]:
                        info_message = self.bill_analyzer.format_trade_statistics(stat)
                        await self.bot.send_message(creds.TELEGRAM_ID, info_message)
                        break
        except Exception as e:
            print(f"Ошибка при отправке уведомления о продаже: {e}")
            self.bot.send_message(creds.TELEGRAM_ID, f"Ошибка при отправке уведомления о продаже: {e}")

    async def _handle_buy_signal(self, coin_info: dict, market_info: List[dict]) -> None:
        """Обработка сигнала на покупку"""
        if coin_info["in_trade"]:
            return

        trade_amount = self._calculate_trade_amount(market_info)
        if not trade_amount:
            return

        success = self._execute_buy_order(coin_info, trade_amount, market_info)
        if success:
            await self._send_buy_notification(coin_info, trade_amount)

    def _calculate_trade_amount(self, market_info: List[dict]) -> Optional[float]:
        """Расчет суммы для торговли"""
        try:
            settings = self.config.read_json("settings")
            usdt_balance = self.asset_manager.get_asset_quantity("USDT")

            if not settings or not settings.get("useFixDeposit", False):
                coins_not_in_trade = sum(1 for coin in market_info if not coin["in_trade"])
                coef = 0.95 / max(coins_not_in_trade, 1)
                amount = round(float(usdt_balance) * coef, 2)
            else:
                amount = float(settings["fixDeposit"])

            return max(amount, 5.02)
        except Exception as e:
            print(f"Ошибка при расчете суммы для торговли: {e}")
            #в качестве дебага
            return 7.0

    def _execute_buy_order(self, coin_info: dict, amount: float, market_info: List[dict]) -> bool:
        try:
            order_id = self.order_manager.buy(coin_info["coin"], amount)
            if order_id:
                coin_info["in_trade"] = True
                self.config.write_json(market_info)
                return True
            return False
        except Exception as e:
            error_msg = f"Ошибка при выполнении ордера на покупку: {e}"
            print(error_msg)
            return False

    async def _send_buy_notification(self, coin_info: dict, amount: float) -> None:
        """Отправка уведомления о покупке"""
        try:
            info_message = (
                f"📅{dt.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"📈{coin_info['coin']} - открыли сделку на {amount} USDT\n"
            )
            await self.bot.send_message(creds.TELEGRAM_ID, info_message)
        except Exception as e:
            print(f"Ошибка при отправке уведомления о покупке: {e}")
            self.bot.send_message(creds.TELEGRAM_ID, f"Ошибка при отправке уведомления о покупке: {e}")

#nest_asyncio.apply()  # Патчинг для работы в Jupyter Notebook

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.start())