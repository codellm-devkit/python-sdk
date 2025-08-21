/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.jsf;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Collection;
import java.util.Date;
import java.util.Iterator;

import javax.annotation.PostConstruct;
import javax.enterprise.context.RequestScoped;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.inject.Inject;
import javax.inject.Named;

import com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean;
import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.FinancialUtils;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Named("marketdata")
@RequestScoped
@Trace
public class MarketSummaryJSF {

  private TradeServices tradeAction;

  private BigDecimal TSIA;
  private BigDecimal openTSIA;
  private double volume;
  private QuoteData[] topGainers;
  private QuoteData[] topLosers;
  private Date summaryDate;

  // cache the gainPercent once computed for this bean
  private BigDecimal gainPercent = null;

  @Inject 
  public MarketSummaryJSF(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }
  
  @PostConstruct
  public void getMarketSummary() {
   
    try {
      MarketSummaryDataBean marketSummaryData = tradeAction.getMarketSummary();
      setSummaryDate(marketSummaryData.getSummaryDate());
      setTSIA(marketSummaryData.getTSIA());
      setVolume(marketSummaryData.getVolume());
      setGainPercent(marketSummaryData.getGainPercent());

      Collection<?> topGainers = marketSummaryData.getTopGainers();

      Iterator<?> gainers = topGainers.iterator();
      int count = 0;
      QuoteData[] gainerjsfs = new QuoteData[5];

      while (gainers.hasNext() && (count < 5)) {
        QuoteDataBean quote = (QuoteDataBean) gainers.next();
        QuoteData r = new QuoteData(quote.getPrice(), quote.getOpen(), quote.getSymbol());
        gainerjsfs[count] = r;
        count++;
      }

      setTopGainers(gainerjsfs);

      Collection<?> topLosers = marketSummaryData.getTopLosers();

      QuoteData[] loserjsfs = new QuoteData[5];
      count = 0;
      Iterator<?> losers = topLosers.iterator();

      while (losers.hasNext() && (count < 5)) {
        QuoteDataBean quote = (QuoteDataBean) losers.next();
        QuoteData r = new QuoteData(quote.getPrice(), quote.getOpen(), quote.getSymbol());
        loserjsfs[count] = r;
        count++;
      }

      setTopLosers(loserjsfs);

    } catch (Exception e) {
      e.printStackTrace();
    }
  }

  public void setTSIA(BigDecimal tSIA) {
    TSIA = tSIA;
  }

  public BigDecimal getTSIA() {
    return TSIA;
  }

  public void setOpenTSIA(BigDecimal openTSIA) {
    this.openTSIA = openTSIA;
  }

  public BigDecimal getOpenTSIA() {
    return openTSIA;
  }

  public void setVolume(double volume) {
    this.volume = volume;
  }

  public double getVolume() {
    return volume;
  }

  public void setTopGainers(QuoteData[] topGainers) {
    this.topGainers = topGainers;
  }

  public QuoteData[] getTopGainers() {
    return topGainers;
  }

  public void setTopLosers(QuoteData[] topLosers) {
    this.topLosers = topLosers;
  }

  public QuoteData[] getTopLosers() {
    return topLosers;
  }

  public void setSummaryDate(Date summaryDate) {
    this.summaryDate = summaryDate;
  }

  public Date getSummaryDate() {
    return summaryDate;
  }

  public void setGainPercent(BigDecimal gainPercent) {
    this.gainPercent = gainPercent.setScale(2,RoundingMode.HALF_UP);
  }

  public BigDecimal getGainPercent() {
    return gainPercent;
  }

  public String getGainPercentHTML() {
    return FinancialUtils.printGainPercentHTML(gainPercent);
  }

}
