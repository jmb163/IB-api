import math

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ContractDetails
import random
from threading import Thread
from queue import Queue
import json
import time
import os
from scipy.stats import norm
from scipy.stats import pearsonr
from scipy.sparse import diags
from scipy.sparse import linalg
import numpy as np

RISK_FREE_RATE = 0.049

def cache_result(fname, original_callback, callback_args):
	if os.path.exists(fname):
		print('USING CACHED RESULT')
		with open(fname, 'r') as f:
			return json.load(f)
	else:
		print('NO CACHED RESULT AVAILABLE, DOING NEW STUFF')
		data = original_callback(*callback_args)
		with open(fname, 'w') as f:
			json.dump(data, f, indent=3)
		return data
class IB(EClient, EWrapper):
	def __init__(self, test_client=False):
		EClient.__init__(self, self)
		self.port = 7496
		if test_client:
			self.port = 7497
		self.client_id = random.randint(0, 255)
		self._req_id = 0
		self.best_exchange = 'IBUSOPT'
		self.requests = {}

	def get_req_id(self):
		self._req_id += 1
		return self._req_id - 1
	def _queue_request(self, req_id, originator=None):
		# print('RECEIVED REQUEST: #{}'.format(req_id))
		if not originator:
			self.requests[str(req_id)] = {
				'queue':Queue(),
				'results':[],
			}
		else:
			self.requests[originator] = {
				'queue':Queue(),
				'results':[]
			}
		# print('KEYS: {}'.format(self.requests.keys()))
		return

	def _set_originator(self, req_id, name):
		# used when many requests could be queued but a select
		# few have to be cleared out
		if str(req_id) in self.requests.keys():
			self.requests[str(req_id)]['originator'] = name
		return

	def _notify_completion(self, req_id, originator=None):
		if str(req_id) in self.requests.keys() and not originator:
			self.requests[str(req_id)]['queue'].put_nowait(req_id)
		else:
			#originator not None
			self.requests[originator]['queue'].put_nowait(originator)
		return
	def _wait_completion(self, req_id, originator=None, timeout=None,
						 cancel_callback=None, cancel_args=(), cancel_kwargs={}):
		if originator:
			nqueue = self.requests[originator]['queue']
			try:
				if timeout:
					nqueue.get(timeout=timeout)
				else:
					nqueue.get()
				result = self.requests[originator]['results']
			except Exception as e:
				if self.requests[originator]['results']:
					result = self.requests[originator]['results']
				else:
					return None
			self.requests.pop(originator)
			if cancel_callback:
				cancel_callback(*cancel_args, **cancel_kwargs)
			return result
		else:
			nqueue = self.requests[str(req_id)]['queue']
			try:
				if timeout:
					nqueue.get(timeout=timeout)
				else:
					nqueue.get()
				result = self.requests[str(req_id)]['results']
			except Exception as e:
				if self.requests[str(req_id)]['results']:
					result = self.requests[str(req_id)]['results'] # return partial or damaged data
				else:
					result = None
			self.requests.pop(str(req_id))
			if cancel_callback:
				cancel_callback(*cancel_args, **cancel_kwargs)
			return result

	def error(self, reqId, errorCode, errorString, advancedOrderRejectJson = ""):
		super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
		# print("ERROR!!! {}".format(errorString))
		if int(errorCode) == 504:
			self.connect_portal()
		if str(reqId) in self.requests.keys():
			self.requests[str(reqId)]['queue'].put_nowait(reqId)
		return

	def connect_portal(self):
		self.connect('127.0.0.1', self.port, self.client_id)

	def current_time(self, time):
		print("The current time is: {}".format(time))

	def contractSymbol(self, symbol, security_type='STK'):
		contract = Contract()
		contract.secType = security_type
		contract.symbol = symbol
		return contract

	def reqContractDetails(self, req_id, contract):
		super().reqContractDetails(req_id, contract)
		self._queue_request(req_id)
		return

	def contractDetails(self, req_id, contract_details, limiter=None):
		super().contractDetails(req_id, contract_details)
		# print("GOT INTO contractDetails")
		if isinstance(contract_details, ContractDetails):
			self.requests[str(req_id)]['results'].append(contract_details.contract)
		return

	def contractDetailsEnd(self, req_id):
		super().contractDetailsEnd(req_id)
		self._notify_completion(req_id)
		return

	def details(self, symbol, security_type='STK'):
		req_id = self.get_req_id()
		self.reqContractDetails(req_id, self.contractSymbol(symbol, security_type=security_type))
		results = self._wait_completion(req_id)
		return results

	def conIds(self, symbol):
		results = self.details(symbol)
		ids = [x.conId for x in results]
		ids = list(set(ids))
		return ids
	def reqSecDefOptParams(self, reqId, underlyingSymbol,
                            futFopExchange, underlyingSecType,
                            underlyingConId):
		super().reqSecDefOptParams(reqId, underlyingSymbol, futFopExchange, underlyingSecType, underlyingConId)
		self._queue_request(reqId)
		return

	def securityDefinitionOptionParameter(self, reqId, exchange,
        underlyingConId, tradingClass, multiplier,
        expirations, strikes):
		super().securityDefinitionOptionParameter(reqId, exchange, underlyingConId,
			tradingClass, multiplier, expirations, strikes)
		ret = {
			'expirations':expirations,
			'strikes':strikes
		}
		self.requests[str(reqId)]['results'] = ret
		return

	def securityDefinitionOptionParameterEnd(self, reqId):
		super().securityDefinitionOptionParameterEnd(reqId)
		self._notify_completion(reqId)
		return

	def reqHistoricalData(self, reqId, contract, endDateTime,
                          durationStr, barSizeSetting, whatToShow,
                          useRTH, formatDate, keepUpToDate, chartOptions):
		super().reqHistoricalData(reqId, contract, endDateTime, durationStr, barSizeSetting,
								whatToShow, useRTH, formatDate, keepUpToDate, chartOptions)
		self._queue_request(reqId)
		return

	def historicalData(self, reqId, bar):
		super().historicalData(reqId, bar)
		self.requests[str(reqId)]['results'].append(bar)
		return

	def historicalDataEnd(self, reqId, start, end):
		super().historicalDataEnd(reqId, start, end)
		self._notify_completion(reqId)
		return

	def last_price(self, symbol, security_type='STK'):
		contract_symbol = self.contractSymbol(symbol, security_type=security_type)
		contract_symbol.exchange = 'SMART'
		contract_symbol.currency = 'USD'
		end_date_time = "" #blank string means, most recent date
		duration_string = "1 D" #amound of data to fetch
		bar_size_setting = "1 day"#the bar granularity
		what_to_show = "TRADES" #show 'last' price
		use_rth = 1 #only pull data from regular trading hours
		format_date = 1
		keep_up_to_date = False
		req_id = self.get_req_id()
		self.reqHistoricalData(req_id, contract_symbol, end_date_time, duration_string,
							   bar_size_setting, what_to_show, use_rth, format_date, keep_up_to_date, [])
		bars = self._wait_completion(req_id)
		if not bars:
			more_details = self.details(symbol)
			for detail in more_details:
				req_id = self.get_req_id()
				contract_symbol.exchange = detail.exchange
				contract_symbol.primaryExchange = detail.exchange
				self.reqHistoricalData(req_id, contract_symbol, end_date_time, duration_string,
								bar_size_setting, what_to_show, use_rth, format_date, keep_up_to_date, [])
				bars = self._wait_completion(req_id)
				if bars:
					break
		if bars:
			return bars[0].close
		else:
			return None

	def option_chain(self, symbol):
		ids = self.conIds(symbol)
		strikes = []
		expirations = []
		for i in range(0, len(ids)):
			# print('Requesting chain for id: {}'.format(ids[i]))
			req_id = self.get_req_id()
			self.reqSecDefOptParams(req_id, symbol, "", "STK", ids[i])
			chain_set = self._wait_completion(req_id)
			if chain_set:
				strikes += list(chain_set['strikes'])
				expirations += list(chain_set['expirations'])
		strikes = sorted(list(set(strikes)))
		expirations = sorted(list(set(expirations)))
		return {'strikes':strikes, 'expirations':expirations}

	def pruned_chain(self, chain, price):
		good_strikes = chain['strikes']
		good_strikes = [x for x in good_strikes if x % 0.5 == 0] #avoid strange strike prices
		closest_strike = min(good_strikes, key=lambda x: abs(x - price))
		closest_strike_index = good_strikes.index(closest_strike)
		strike_width = 16 #pick the 11 closest strikes on each side
		end = min(closest_strike_index + strike_width, len(good_strikes))
		upper_strikes = good_strikes[closest_strike_index:end]
		begin = max(0, closest_strike_index - strike_width)
		lower_strikes = good_strikes[begin:closest_strike_index]
		best_strikes = list(set(list(upper_strikes + lower_strikes)))
		return best_strikes

	def reqMktData(self, reqId, contract, genericTickList, snapshot,
				   regulatorySnapshot, mktDataOptions):
		self._queue_request(reqId)
		super().reqMktData(reqId, contract, genericTickList, snapshot,
						   regulatorySnapshot, mktDataOptions)
		return

	def tickOptionComputation(self, reqId, tickType, tickAttrib,
            impliedVol, delta, optPrice, pvDividend,
            gamma, vega, theta, undPrice):
		super().tickOptionComputation(reqId, tickType, tickAttrib, impliedVol, delta, optPrice, pvDividend,
									  gamma, vega, theta, undPrice)
		# print("GOT TO TICK OPTION")
		def valid_ret(val):
			if not val['delta']:
				return False
			if not val['vega']:
				return False
			if not val['implied_volatility']:
				return False
			if not val['theta']:
				return False
			if not val['gamma']:
				return False
			return True
		ret = {
			'delta':delta,
			'gamma':gamma,
			'vega':vega,
			'theta':theta,
			'price':optPrice,
			'underlying_price':undPrice,
			'implied_volatility':impliedVol,
			'dividend_prices':pvDividend
		}
		# print(json.dumps(ret, indent=3))
		if valid_ret(ret):
			self.cancelMktData(reqId)
			if str(reqId) in self.requests.keys():
				self.requests[str(reqId)]['results'] = ret
		return

	def tickSnapshotEnd(self, reqId):
		super().tickSnapshotEnd(reqId)
		self._notify_completion(reqId)
		return

	def options_exchange(self, expiration, right, symbol):
		contract_symbol = self.contractSymbol(symbol)
		contract_symbol.secType = "OPT"
		contract_symbol.currency = "USD"
		contract_symbol.lastTradeDateOrContractMonth = expiration
		contract_symbol.right = right
		req_id = self.get_req_id()
		self.reqContractDetails(req_id, contract_symbol)
		contracts = self._wait_completion(req_id)
		exchanges = [x.exchange for x in contracts]
		return list(set(exchanges))
	def options_contract_details(self, strike, expiration, option_type, symbol, exchanges=None):
		'''
		Request the greeks and price for a single option contract
		:param strike:
		:param expiration:
		:param option_type:
		:param symbol:
		:param exchanges:
		:return:
		'''
		contract_symbol = self.contractSymbol(symbol)
		contract_symbol.secType = "OPT"
		contract_symbol.currency = "USD"
		contract_symbol.exchange = self.best_exchange
		contract_symbol.lastTradeDateOrContractMonth = expiration
		contract_symbol.strike = strike
		contract_symbol.right = option_type
		req_id = self.get_req_id()
		self.reqMktData(req_id, contract_symbol, "", False, False, [])
		details = self._wait_completion(req_id, timeout=3)
		if exchanges and not details:
			for exchange in exchanges:
				print("TRYING A DIFFERENT EXCHANGE")
				req_id = self.get_req_id()
				contract_symbol.exchange = exchange
				self.reqMktData(req_id, contract_symbol, "", False, False, [])
				details = self._wait_completion(req_id, timeout=3)
				if details:
					self.best_exchange = exchange #set the new exchange to whatever works for the time
					break
		self.cancelMktData(req_id) #just in case
		return details

	def full_options_chain(self, symbol):
		'''
		:param sybmol:   ticker symbol for which to get all of the data
		:return: A json object like so:
		{'expirations':{
			'20230621':{
				'280.0':{
					'put':options_contract_details(280, '20230621', 'PUT', symbol),
					'call':options_contract_detail(280, '20230621', 'CALL', symbol)
				}git push --set-upstream origin master
				...
			}
			...
			'20230821':[ ....
		}
		expiries: list of expirations in YYYYMMdd format
		strikes:  list of strikes [ 280.0, 282.5, 285.0 ... ]
		'''
		details = cache_result('full_chain_skeleton.json', self.option_chain, (symbol,))
		expiries = details['expirations']
		expiries = sorted(expiries)[0:7]
		price = cache_result('full_chain_last_price.json', self.last_price, (symbol,))
		strikes = cache_result('full_chain_pruned.json', self.pruned_chain, (details, price,))
		exchanges = cache_result("full_chain_exchanges.json", self.options_exchange, (expiries[0], 'CALL', symbol,))
		print('GOT THE PRUNED CHAIN AND LAST PRICE')
		# self.reqMarketDataType(3) #send delayed data, hopefully less taxing on system
		# print("PRICE: $ {}".format(price))
		# print(json.dumps(strikes, indent=3))
		chain = {}
		chain['expirations'] = {}
		with RequestContext(self, chain):
			for cycle in expiries:
				chain['expirations'][cycle] = {}
				for strike in strikes:
					chain['expirations'][cycle][str(float(strike))] = {}
					put = self.options_contract_details(float(strike), cycle, 'PUT', symbol, exchanges=exchanges)
					call = self.options_contract_details(float(strike), cycle, 'CALL', symbol, exchanges=exchanges)
					time.sleep(3)
					chain['expirations'][cycle][str(float(strike))]['put'] = put
					chain['expirations'][cycle][str(float(strike))]['call'] = call
					print("EXPIRATION: {} STRIKE: $ {}".format(cycle, strike))
					print(json.dumps(chain['expirations'][cycle][str(float(strike))], indent=3))
		return chain

	def daily_data(self, symbol, ndays=100, security_type='STK', contract=None):
		'''
		Get the daily ticks for a given stock. This will be used to calculate the nth day
		historical volatility, or if more data is requested than the nth day volatility then
		perhaps a smoothed volatility calculation could be created from the data

		It may be more convenient to get data for 400 or so days back, and then calculate the
		100 day volatility for each available 100 day window
		:param symbol: The stock symbol for which to get the historical data
		:param ndays:  The number of days to reach back
		:return:       An array of tick data, could be OHLC or just C
		'''
		if contract is None:
			contract_symbol = self.contractSymbol(symbol, security_type=security_type)
			contract_symbol.exchange = 'SMART'
			contract_symbol.currency = 'USD'
		else:
			contract_symbol = contract
		end_date_time = ""  # blank string means, most recent date
		if ndays > 365:
			duration_string = "{} Y".format((ndays // 365) + 1)
		else:
			duration_string = "{} D".format(ndays)  # amound of data to fetch
		bar_size_setting = "1 day"  # the bar granularity
		what_to_show = "TRADES"  # show 'last' price
		use_rth = 1  # only pull data from regular trading hours
		format_date = 1
		keep_up_to_date = False
		req_id = self.get_req_id()
		self.reqHistoricalData(req_id, contract_symbol, end_date_time, duration_string,
								bar_size_setting, what_to_show, use_rth, format_date, keep_up_to_date, [])
		bars = self._wait_completion(req_id)
		if not bars:
			more_details = self.details(symbol)
			for detail in more_details:
				req_id = self.get_req_id()
				# contract_symbol.exchange = detail.exchange
				# contract_symbol.primaryExchange = detail.exchange
				contract_symbol = detail
				self.reqHistoricalData(req_id, contract_symbol, end_date_time, duration_string,
								bar_size_setting, what_to_show, use_rth, format_date, keep_up_to_date, [])
				bars = self._wait_completion(req_id)
				if bars:
					break
		if not bars:
			return []
		bars = [{
			'close':x.close,
			'time':x.date
		} for x in bars]
		bars = sorted(bars, key=lambda x: x['time'])
		return bars

	def reqPositions(self):
		super().reqPositions()
		# not actually req id 0, it will key off of the position
		self._queue_request(0, originator='position')
		return

	def _contract_to_dict(self, contract):
		'''
		this should be enough information to work backwards from
		:param contract:
		:return:
		'''
		def combo_legs_to_dict(legs):
			if not legs:
				return None
			combo = []
			for leg in legs:
				cm = {
					'id':leg.conId,
					'action':leg.action
				}
				combo.append(cm)
			return combo
		ret = {
			'security_type': contract.secType,
			'exchange': contract.exchange,
			'currency': contract.currency,
			'multiplier': contract.multiplier,
			'symbol': contract.symbol,
			'id': contract.conId,
			'combo': combo_legs_to_dict(contract.comboLegs)
		}
		return ret
	def position(self, account, contract, position,
                 avgCost):
		super().position(account, contract, position, avgCost)
		ret = {
			'account':account,
			'contract':self._contract_to_dict(contract),
			'position':int(str(position)),
			'average_cost':avgCost
		}
		originator = 'position'
		self.requests[originator]['results'].append(ret)
		return

	def positionEnd(self):
		super().positionEnd()
		originator = 'position'
		self._notify_completion(0, originator=originator)

	def positions(self):
		'''
		Get the current account's positions
		this is the first part of the machinery for
		automatic hedging
		:return: [Position, ...]
		'''
		self.reqPositions()
		originator = 'position'
		#give a time out so we don't stay subscribed to positions forever
		results = self._wait_completion(0, originator=originator, timeout=2, cancel_callback=self.cancelPositions)
		return results

# def volatility(price_series, annualized=False, length=None):
# 	if not length:
# 		length = len(price_series)
# 	pdiffs = []
# 	for i in range(1, length):
# 		pdiffs.append(math.log(price_series[i]/price_series[i - 1]))
# 	mean = sum(pdiffs)/(length - 1)
# 	variance = sum([(x - mean)**2 for x in pdiffs])/(length - 1)
# 	vol = math.sqrt(variance)
# 	if annualized:
# 		vol = math.sqrt(256) * vol
# 	return vol

def volatility(price_series, annualized=False, length=None):
	if not length:
		length = len(price_series)
	nl_diffs = []
	for i in range(1, length):
		nl_diffs.append(math.log(price_series[i]/price_series[i-1]))
	nl_arr = np.array(nl_diffs)
	variance = float(np.var(nl_arr))
	vol = math.sqrt(variance) * math.sqrt(260) * math.sqrt(length/260) * 100
	return vol



def volatility_schedule(price_series):
	'''
	This will figure the percentile volatilities for windows of
	10, 20, 50, and 100 day windows
	:param price_series:
	:return:
	'''
	series_len = len(price_series)
	def window(series, size):
		vols = []
		start = 0
		finish = size
		while finish < series_len:
			vols.append(volatility(series[start:finish], length=size))
			start += 1
			finish += 1
		return vols
	def scale(vol_series):
		'''
		vol series need to be sorted
		:param vol_series:
		:return:
		'''
		p_keys = list(range(0, 105, 5))
		vol_series_len = len(vol_series)
		ret = {}
		ret[str(p_keys[0])] = vol_series[0]
		# print(vol_series_len)
		for i in range(1, 20):
			ind = int((p_keys[i]/100) * vol_series_len)
			ret[str(p_keys[i])] = vol_series[ind]
		ret[str(p_keys[-1])] = vol_series[-1]
		return ret

	vols_100 = window(price_series, 100)
	vols_50 = window(price_series, 50)
	vols_20 = window(price_series, 20)
	vols_10 = window(price_series, 10)
	print("GOT PAST WINDOW")
	vols_100_len = len(vols_100)
	vols_50_len = len(vols_50)
	vols_20_len = len(vols_20)
	vols_10_len = len(vols_10)
	vols_100_sorted = sorted(vols_100)
	vols_50_sorted = sorted(vols_50)
	vols_20_sorted = sorted(vols_20)
	vols_10_sorted = sorted(vols_10)
	front_100 = vols_100[-1]
	front_50 = vols_50[-1]
	front_20 = vols_20[-1]
	front_10 = vols_10[-1]
	vols_100_front_percentile = vols_100_sorted.index(front_100)/vols_100_len
	vols_50_front_percentile = vols_50_sorted.index(front_50)/vols_50_len
	vols_20_front_percentile = vols_20_sorted.index(front_20)/vols_20_len
	vols_10_front_percentile = vols_10_sorted.index(front_10)/vols_10_len
	ret = {
		'100_day_percentile':vols_100_front_percentile,
		'50_day_percentile':vols_50_front_percentile,
		'20_day_percentile':vols_20_front_percentile,
		'10_day_percentile':vols_10_front_percentile,
		'100_day':front_100,
		'50_day':front_50,
		'20_day':front_20,
		'10_day':front_10,
		'100_day_scale':scale(vols_100_sorted),
		'50_day_scale':scale(vols_50_sorted),
		'20_day_scale':scale(vols_20_sorted),
		'10_day_scale':scale(vols_10_sorted)
	}
	return ret

def correlation_schedule(price_series_a, price_serires_b):
	'''
	Do a similar analysis to the volatility schedule but with correlations.
	This is most useful for checking how an instruments correlation with another
	may vary from time to time. For instance, a stock probably will have a negative
	correlation to the VIX, but for the sake of being thorough, that should be
	checked to see if there's drift, or the median correlation over a select number
	of time windows is not what might be expected

	It is assumed that price_series_a and price_series_b are of the same length and
	are matching in time
	:param price_series_a:
	:param price_serires_b:
	:return:
	'''
	series_len = len(price_series_a)
	def window(sa, sb, size):
		corrs = []
		start = 0
		finish = size
		while finish < series_len:
			corrs.append(correlation(sa[start:finish], sb[start:finish]))
			start += 1
			finish += 1
		return corrs

	def scale(corr_series):
		'''
		vol series need to be sorted
		:param vol_series:
		:return:
		'''
		p_keys = list(range(0, 105, 5))
		vol_series_len = len(corr_series)
		ret = {}
		ret[str(p_keys[0])] = corr_series[0]
		for i in range(1, 20):
			ind = int((p_keys[i]/100) * vol_series_len)
			ret[str(p_keys[i])] = corr_series[ind]
		ret[str(p_keys[-1])] = corr_series[-1]
		return ret

	corrs_100 = window(price_series_a, price_serires_b, 100)
	corrs_50 = window(price_series_a, price_serires_b, 50)
	corrs_20 = window(price_series_a, price_serires_b, 20)
	corrs_10 = window(price_series_a, price_serires_b, 10)
	corrs_100_len = len(corrs_100)
	corrs_50_len = len(corrs_50)
	corrs_20_len = len(corrs_20)
	corrs_10_len = len(corrs_10)
	corrs_100_sorted = sorted(corrs_100, reverse=True)
	corrs_50_sorted = sorted(corrs_50, reverse=True)
	corrs_20_sorted = sorted(corrs_20, reverse=True)
	corrs_10_sorted = sorted(corrs_10, reverse=True)
	front_100 = corrs_100[-1]
	front_50 = corrs_50[-1]
	front_20 = corrs_20[-1]
	front_10 = corrs_10[-1]
	corrs_100_front_percentile = corrs_100_sorted.index(front_100) / corrs_100_len
	corrs_50_front_percentile = corrs_50_sorted.index(front_50) / corrs_50_len
	corrs_20_front_percentile = corrs_20_sorted.index(front_20) / corrs_20_len
	corrs_10_front_percentile = corrs_10_sorted.index(front_10) / corrs_10_len
	ret = {
		'100_day_percentile': corrs_100_front_percentile,
		'50_day_percentile': corrs_50_front_percentile,
		'20_day_percentile': corrs_20_front_percentile,
		'10_day_percentile': corrs_10_front_percentile,
		'100_day': front_100,
		'50_day': front_50,
		'20_day': front_20,
		'10_day': front_10,
		'100_day_scale': scale(corrs_100_sorted),
		'50_day_scale': scale(corrs_50_sorted),
		'20_day_scale': scale(corrs_20_sorted),
		'10_day_scale': scale(corrs_10_sorted)
	}
	return ret


def option_probabilities(current_price, cross_price, volatility, time):
	'''
	Calculate the probability of a stock price being above or below some value given
	the price and volatility, and assuming that prices are lognormal (they aren't really
	but that's okay

	Crossing probabilities are more involved, but given that it's desirable to use stop
	losses, it's important to gauge the probability that a stop may be hit
	Particle Method:
	erfc(x) -> math.erf(x) Gauss error function
	Pcross = P1 + P2
	P1 = (1/2) * math.erf((1/math.sqrt(2))*((x/math.sqrt(t)) - (v * math.sqrt(t)))
	P2 = (exp(2 * v * x)/2) * math.erf((1/math.sqrt(2))* ((x/math.sqrt(t)) + (v * math.sqrt(t))))

	Browning Motion Based Probability:
	(for call)
	Pcross = Ncdf( (m - a * t)/sqrt(t) ) - exp(2 * a * m) * Ncdf( (-m - a * t)/sqrt(t) )
	(for put, crossing below requires adding the second term)

	m = ln(K/S0)/sigma

	a = ( r - (1/2)*sigma**2 )/sigma

	Barrier Breach:
	Pc = ((X/S)^(mu + lambda)) * Ncdf(-z) + ((X/S)^(mu - lambda)) * Ncdf(-z + 2 * lambda * sigma * math.sqrt(T))
	Pp = ((X/S)^(mu + lambda)) * Ncdf(z) + ((X/S)^(mu - lambda)) * Ncdf(z - 2 * lambda * sigma * math.sqrt(T))
	where:
		z = ((ln(X/S)/(sigma * math.sqrt(T)) + lambda * sigma * math.sqrt(T)

		mu = (b - (sigma**2)/2)/(sigma**2)

		lambda = math.sqrt(mu**2 + (2r/(sigma**2)))

	Pbelow (at expiration):
		Ncdf( ( ln( q/p ) ) / vt )
		 q is current price
		 p is target price
		 vt is v * sqrt(t)
		 t is a decimal of a year
	Pabove (at expiration)
	    1 - Pbelow
	volatility is sigma squared

	:param current_price: The current price of the stock in question
	:param cross_price: The price in question that we are to calculate the probability of crossing
	:param volatility: The volatility, calculated as the squared variance
	:param time: the time given in days, will be converted to some portion of a year
	:return:
	'''
	# simple probability first
	t = time/365 #256 trading days in a year
	vt = volatility * math.sqrt(t)
	p_below = norm.cdf(math.log(cross_price/current_price)/vt)
	p_above = 1 - p_below
	# p_cross is much more complicated
	'''
	Pcross = Ncdf( (m - a * t)/sqrt(t) ) - exp(2 * a * m) * Ncdf( (-m - a * t)/sqrt(t) )
	(for put, crossing below requires adding the second term)

	m = ln(K/S0)/sigma

	a = ( r - (1/2)*sigma**2 )/sigma
	'''
	m = math.log(cross_price/current_price)/volatility
	r = 0.05 #risk free rate, just say that it's roughly 2-year / overnight interest
	a = (r - (1/2)*volatility**2)/volatility
	p_cross_c = norm.cdf((m - a * t)/math.sqrt(t)) - (math.exp(2 * a * m) * norm.cdf((-m - a * t)/math.sqrt(t)))
	p_cross_p = norm.cdf((m - a * t)/math.sqrt(t)) + (math.exp(2 * a * m) * norm.cdf((m + a * t)/math.sqrt(t)))
	if cross_price > current_price:
		p_cross = 1 - p_cross_c
	else:
		p_cross = p_cross_p
	ret = {
		"probability_below":p_below,
		"probability_above":p_above,
		"probability_touch":p_cross
	}
	return ret

def correlation(series_a_in, series_b_in):
	series_a_len = len(series_a_in)
	series_b_len = len(series_b_in)
	series_len = min(series_a_len, series_b_len)
	if series_a_len > series_b_len:
		series_a = series_a_in[(-1 * series_b_len):]
		series_b = series_b_in
	elif series_b_len > series_a_len:
		series_b = series_b_in[(-1 * series_a_len):]
		series_a = series_a_in
	else:
		series_a = series_a_in
		series_b = series_b_in
	series_a = np.array(series_a)
	series_b = np.array(series_b)
	coefficient = pearsonr(series_a, series_b)[0]
	return coefficient

def hedge_ratio(v1, v2, p1, p2, u1, u2, d1, d2, m1, m2):
	'''
	Examples:
	* /ES future
	-v 30 (historical volatility, maybe median over some windows)
	-p 4330 price
	-u (for futures, set unit as multiplier, i.e. the value of 1 point)
	-d 1 (1 contract is 1 delta)
	* SPY (STOCK ETF)
	-v 30
	-p 430.30
	-u 1
	-d 0.1

	30 * 4330.0 * 1 * 1 * 50
	----------------------------- = 500
	30 * 433.00 * 1 * 0.01 * 100
	(this means that if the volatility is the same, it takes 1000 SPY to hedge
	one ES future, obviously this isn't great in terms of maintenance margin so maybe
	consider using /MES futures or some options on /ES with an offsetting delta.)

	Note:
	Since there are some peculiarities when comparing various types of underlyings, there
	are a few heuristics to arrive at the correct ratio

	- 1 share of stock is 1 delta
	- 1 futures contract is 100 delta
	- The futures multiplier is the value of 1 point (i.e. 1 point in /ES is $50 mult is 50)
	- For compatibility between futures and stock or other underlyings, consider the multiplier of a
	  stock position to be 100
	- Units is the number of share or contracts an option represents (1 for things that aren't options)


	Figure out the hedge ratio for two different underlyings
	:param v1: volatility of underlying 1
	:param v2: volatility of underlying 2
	:param p1: price of underlying 1
	:param p2: price of underlying 2
	:param u1: trading unit of underlying 1
	:param u2: trading unit of underlying 2
	:param d1: delta of underlying 1
	:param d2: delta of underlying 2
	:param m1: multiplier of underlying 1
	:param m2: multiplier of underlying 2
	:return:
	'''
	return (v1 * p1 * u1 * d1 * m1)/(v2 * p2 * u2 * d2 * m2)

def d1_calc(stock_price, strike_price, volatility, time, dividend_yield=0):
	'''
	:param stock_price:
	:param strike_price:
	:param volatility:
	:param time:
	:return:
	'''
	numerator_p1 = np.log(np.divide(stock_price, strike_price))
	numerator_p2 = np.multiply(time, (RISK_FREE_RATE - dividend_yield + np.divide(np.square(volatility), 2)))
	# numerator_p1 = math.log(stock_price / strike_price)
	# numerator_p2 = time * (RISK_FREE_RATE - dividend_yield + (volatility**2/2))
	numerator = np.add(numerator_p1, numerator_p2)
	denominator = np.multiply(volatility, np.sqrt(time))
	return np.divide(numerator, denominator)

def d2_calc(d1, volatility, time):
	'''
	:param d1: the d1 value
	:param volatility: given volatility of option (historical volatility or implied volatility)
	:param time: time, given as percentage of year (i.e. days/365)
	:return:
	'''
	return np.subtract(d1, np.multiply(volatility, np.sqrt(time)))
def put_price(stock_price, strike_price, volatility, time, dividend_yield=0):
	'''
	:param stock_price:
	:param strike_price:
	:param volatility:
	:param dividen_yield:
	:return:
	'''
	d1 = d1_calc(stock_price, strike_price, volatility, time, dividend_yield=dividend_yield)
	d2 = d2_calc(d1, volatility, time)
	print("d1: {} d2: {} delta: {}".format(d1, d2, norm.cdf(d1)))
	part1 = np.multiply(np.multiply(np.exp(np.multiply(-RISK_FREE_RATE, time)), norm.cdf(-d2)), strike_price)
	part2 = np.multiply(np.multiply(np.exp(np.multiply(-dividend_yield, time)), norm.cdf(-d1)), stock_price)
	# part1 = (strike_price * np.exp(-RISK_FREE_RATE * time) * norm.cdf(-d2))
	# part2 = (stock_price * np.exp(-dividend_yield * time) * norm.cdf(-d1))
	return np.subtract(part1, part2)

def call_price(stock_price, strike_price, volatility, time, dividend_yield=0):
	'''
	:param stock_price: current stock price
	:param strike_price: strike price of option in question
	:param volatility: given volatility of option (historical volatility or implied volatility)
	:param time: time, given as percentage of the year (i.e. days/365)
	:return:
	'''
	d1 = d1_calc(stock_price, strike_price, volatility, time, dividend_yield=dividend_yield)
	d2 = d2_calc(d1, volatility, time)
	print("d1: {} d2: {}".format(d1, d2))
	part1 = np.multiply(np.multiply(np.exp(np.multiply(-dividend_yield, time)), norm.cdf(d1)), stock_price)
	part2 = np.multiply(np.multiply(np.exp(np.multiply(-RISK_FREE_RATE, time)), norm.cdf(d2)), strike_price)
	return np.subtract(part1, part2)

def monte_carlo(start_price, volatility, days, simulations, dt=1, risk_free_rate=0.049, reshape=True):
	days_in_year = 365 # or 252
	sigma = np.divide(volatility, np.sqrt(days_in_year))
	rate = risk_free_rate / days_in_year # pro rata
	epsilon = np.random.normal( size=((days * simulations) + (simulations - 1)))
	ds_s = np.add(np.multiply(rate, dt), np.multiply(sigma, np.multiply(np.sqrt(dt), epsilon)))
	ones = -np.ones((simulations * days) + simulations)
	ones[0:-1:(days + 1)] = 1

	ds_s[days:((days * simulations) + 1):(days + 1)] = -1
	d = [ds_s + 1, ones]
	k = [-1, 0]
	M = diags(d, k, format='csc')
	p = np.zeros(((days * simulations) + simulations, 1))
	p[0:-1:(days + 1)] = start_price
	s = linalg.spsolve(M, p)

	if reshape:
		s = np.reshape(s, (simulations, days + 1))

	return s




class RequestContext(IB):
	'''
	Some requests are larger and might need some cleanup
	'''
	def __init__(self, app, chain):
		self.app = app
		self.chain = chain
		pass
	def __enter__(self):
		self.app.reqMarketDataType(2)
		return

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.app.reqMarketDataType(1)
		# if self.app.requests.keys():
		# 	for key in self.app.requests.keys():
		# 		rt = self.app.requests.pop(key)
		# 		rt['queue'].put_nowait('SHUTDOWN')
		try:
			with open("partial_request.json", 'w') as f:
				json.dump(self.chain, f, indent=3)
		except Exception as e:
			pass
		return


def run_loop(app):
	app.run()

