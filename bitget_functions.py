from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from dataclasses import dataclass
from bitget.exceptions import BitgetAPIException

@dataclass
class TradeBill:
    deal_type: str
    coin_quantity: float
    coin: str
    usdt_quantity: float
    ctime: int
    fees: float

@dataclass
class TradeStatistics:
    month: int
    year: int
    start_date: str
    end_date: str
    duration: Union[int, str]
    coin: str
    coin_quantity: float
    usdt_buy_quantity: float
    usdt_sell_quantity: float
    income: float
    income_percent: float

class OrderManager:
    def __init__(self, order_api):
        self.order_api = order_api

    def _create_order_params(self, coin: str, side: str, size: float) -> Dict:
        """
        Создаем параметры ордера с учетом требований API.
        Для market buy - size в USDT
        Для market sell - size в базовой монете
        """
        return {
            "symbol": f"{coin}USDT",
            "side": side,
            "orderType": "market",
            "size": str(size),
            "force": "ioc"
        }

    def execute_order(self, coin: str, size: float, side: str) -> str:
        try:
            params = self._create_order_params(coin, side, size)
            print(f"Executing {side} order for {coin}")
            print(f"Order params: {params}")
            
            response = self.order_api.placeOrder(params)
            print(f"Order response: {response}")
            
            if response.get("code") == "00000":
                return response["data"]["orderId"]
            else:
                print(f"Error placing order: {response.get('msg')}")
                return ""
        except Exception as e:
            print(f"Error: {e}")
            return ""

    def buy(self, coin: str, size: float) -> str:
        """
        Создает ордер на покупку
        :param coin: Торговая пара (например, 'BTC')
        :param size: Количество USDT для покупки
        """
        return self.execute_order(coin, size, "buy")

    def sell(self, coin: str, size: float) -> str:
        """
        Создает ордер на продажу
        :param coin: Торговая пара (например, 'BTC')
        :param size: Количество монеты для продажи
        """
        return self.execute_order(coin, size, "sell")

    def get_order_info(self, order_id: str) -> Dict:
        try:
            response = self.order_api.orderInfo({"orderId": order_id})
            print(response)
            return response
        except BitgetAPIException as e:
            print(f"Error: {e.message}")
            return {}

class AssetManager:
    def __init__(self, account_api, market_api):
        self.account_api = account_api
        self.market_api = market_api

    def get_asset_quantity(self, coin: str) -> float:
        try:
            response = self.account_api.assets({"coin": coin})
            return float(response["data"][0]["available"])
        except BitgetAPIException as e:
            print(f"Error: {e.message}")
            return 0.0

    def get_ticker_price(self, coin: str) -> float:
        try:
            response = self.market_api.tickers({"symbol": f"{coin}USDT"})
            return float(response["data"][0]["lastPr"])
        except BitgetAPIException as e:
            print(f"Error: {e.message}")
            return 0.0

    def get_all_assets(self) -> List:
        try:
            return self.account_api.assets({})["data"]
        except BitgetAPIException as e:
            print(f"Error: {e.message}")
            return []

    def format_assets_message(self, assets: List) -> str:
        format_message = "Монеты на аккаунте:\n"
        usdt_message = ""

        for asset in assets:
            if asset["available"] == "0.00000000":
                continue

            if asset["coin"] != "USDT":
                price = self.get_ticker_price(asset["coin"])
                size = round(price * float(asset["available"]), 2)
                format_message += f"💵{float(asset['available'])} {asset['coin']} ~= {size} USDT💵\n"
            else:
                usdt_message += f"💲{round(float(asset['available']), 2)} {asset['coin']}💲\n"

        return format_message + usdt_message

class BillAnalyzer:
    MONTHS = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }

    def __init__(self, account_api):
        self.account_api = account_api

    def get_account_bills(self, business_type: str, days: int) -> List:
        try:
            start_time = int((datetime.now() - timedelta(days)).timestamp() * 1000)
            params = {
                "startTime": str(start_time),
                "businessType": business_type
            }
            response = self.account_api.bills(params)
            return response["data"]
        except BitgetAPIException as e:
            print(f"error: {e.message}")
            return ""

    def process_bill(self, asset: Dict, usdt_assets: List) -> TradeBill:
        usdt_quantity = sum(
            abs(float(usdt_asset["size"]))
            for usdt_asset in usdt_assets
            if usdt_asset["coin"] == 'USDT' and usdt_asset["bizOrderId"] == asset["bizOrderId"]
        )

        deal = "Покупка" if asset["businessType"] == 'ORDER_DEALT_IN' else "Продажа"
        
        return TradeBill(
            deal_type=deal,
            coin_quantity=abs(float(asset["size"])),
            
            coin=asset["coin"],
            usdt_quantity=usdt_quantity,
            ctime=int(asset["cTime"]),
            fees=abs(float(asset["fees"]))
        )

    @staticmethod
    def format_trade_statistics(stat: TradeStatistics) -> str:
        status = "✅" if stat.income > 0 else "❌" if stat.income < 0 else "➡️"
        return (
            f"{status} {stat.coin} ({stat.coin_quantity})\n"
            f"📅 {stat.start_date} - {stat.end_date}\n"
            f"💰 Вход: {stat.usdt_buy_quantity} USDT\n"
            f"💵 Выход: {stat.usdt_sell_quantity} USDT\n"
            f"📊 Прибыль: {stat.income} USDT ({stat.income_percent}%)\n"
            f"⏱ Длительность: {stat.duration} дней\n"
            f"{'=' * 30}\n"
        )

    async def process_bills(self, days: int) -> tuple[List[TradeStatistics], Dict]:
        # Получаем все сделки
        buy_bills = self.get_account_bills('ORDER_DEALT_IN', days)
        sell_bills = self.get_account_bills('ORDER_DEALT_OUT', days)
        usdt_bills = self.get_account_bills('USDT', days)

        # Обрабатываем сделки
        processed_buy_bills = [
            self.bill_analyzer.process_bill(bill, usdt_bills)
            for bill in buy_bills
        ]
        processed_sell_bills = [
            self.bill_analyzer.process_bill(bill, usdt_bills)
            for bill in sell_bills
        ]

        # Создаем статистику по парам сделок
        stats = []
        coins_in_trade = {}
        
        for buy_bill in processed_buy_bills:
            # Ищем соответствующую сделку продажи
            matching_sell = next(
                (sell for sell in processed_sell_bills 
                if sell.coin == buy_bill.coin 
                and sell.ctime > buy_bill.ctime),
                None
            )
            
            stat = self.create_statistics(buy_bill, matching_sell)
            
            if matching_sell:
                stats.append(stat)
            else:
                coins_in_trade[buy_bill.coin] = stat

        return stats, coins_in_trade

    def create_statistics(self, bill: TradeBill, sell_bill: Optional[TradeBill] = None) -> TradeStatistics:
        buy_date = datetime.fromtimestamp(bill.ctime/1000.0)
        sell_date = datetime.fromtimestamp(sell_bill.ctime/1000.0) if sell_bill else None

        usdt_buy_quantity = round(float(bill.usdt_quantity) + float(bill.fees), 2)
        usdt_sell_quantity = round(float(sell_bill.usdt_quantity) if sell_bill else 0, 2)

        return TradeStatistics(
            month=int(sell_date.strftime("%m")) if sell_bill else 0,
            year=int(sell_date.strftime("%Y")) if sell_bill else 0,
            start_date=buy_date.strftime("%d.%m.%Y %H:%M"),
            end_date=sell_date.strftime("%d.%m.%Y %H:%M") if sell_bill else "",
            duration=(sell_date - buy_date).days if sell_bill else "сделка в процессе",
            coin=bill.coin,
            coin_quantity=float(bill.coin_quantity),
            usdt_buy_quantity=usdt_buy_quantity,
            usdt_sell_quantity=usdt_sell_quantity,
            income=round(usdt_sell_quantity - usdt_buy_quantity, 2) if sell_bill else 0,
            income_percent=round((usdt_sell_quantity - usdt_buy_quantity) / usdt_buy_quantity * 100, 2) if sell_bill else 0
        )

    def format_monthly_statistics(self, stats: List[TradeStatistics]) -> str:
        if not stats:
            return "Нет данных для анализа"

        current_month = {"month": 0, "year": 0}
        metrics = {
            "income": 0, "all_percent": 0, "profit_count": 0,
            "loss_count": 0, "max_loss_percent": 0
        }
        
        result = "Статистика по месяцам:\n"

        for stat in stats:
            if current_month["month"] != stat.month:
                if current_month["month"] != 0:
                    result += self._format_month_summary(current_month, metrics)
                    metrics = {
                        "income": 0, "all_percent": 0, "profit_count": 0,
                        "loss_count": 0, "max_loss_percent": 0
                    }
                current_month = {"month": stat.month, "year": stat.year}

            self._update_metrics(metrics, stat)

        if current_month["month"] != 0:
            result += self._format_month_summary(current_month, metrics)

        return result

    def _format_month_summary(self, current_month: Dict, metrics: Dict) -> str:
        total_deals = metrics["profit_count"] + metrics["loss_count"]
        avg_percent = round(metrics["all_percent"] / total_deals, 2) if total_deals > 0 else 0
        profit_percent = round((metrics["profit_count"] * 100) / total_deals, 2) if total_deals > 0 else 0

        return (
            f"ℹ️Данные за {self.MONTHS.get(current_month['month'], '')} {current_month['year']} г.:ℹ️\n"
            f"💶Чистая прибыль - {round(metrics['income'], 2)} UDST💶\n"
            f"💵Чистая прибыль% - {round(metrics['all_percent'], 2)}%💵\n"
            f"🤝Закрытых сделок - {total_deals}🤝\n"
            f"📈Процент прибыльных - {profit_percent}%📈\n"
            f"📉Максимальная просадка - {metrics['max_loss_percent']}%📉\n"
            f"💰Средний доход по сделке - {avg_percent}%💰\n\n"
        )

    def _update_metrics(self, metrics: Dict, stat: TradeStatistics) -> None:
        metrics["income"] += stat.income
        metrics["all_percent"] += stat.income_percent
        
        if stat.income_percent > 0:
            metrics["profit_count"] += 1
        elif stat.income_percent < 0:
            metrics["loss_count"] += 1
            metrics["max_loss_percent"] = min(metrics["max_loss_percent"], stat.income_percent)