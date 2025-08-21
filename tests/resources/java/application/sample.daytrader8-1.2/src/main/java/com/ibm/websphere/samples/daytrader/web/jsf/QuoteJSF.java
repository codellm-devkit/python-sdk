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

import javax.annotation.PostConstruct;
import javax.enterprise.context.RequestScoped;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.faces.component.html.HtmlDataTable;
import javax.faces.context.ExternalContext;
import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Named("quotedata")
@RequestScoped
@Trace
public class QuoteJSF {

  @Inject
  private ExternalContext context;

  private TradeServices tradeAction;

  private QuoteData[] quotes;
  private String symbols = null;
  private HtmlDataTable dataTable;
  private Integer quantity = 100;

  @Inject 
  public QuoteJSF(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  @PostConstruct
  public void getAllQuotes() {
    getQuotesBySymbols();
  }

  public String getQuotesBySymbols() {
    HttpSession session = (HttpSession) context.getSession(true);

    if (symbols == null && (session.getAttribute("symbols") == null)) {
      setSymbols("s:0,s:1,s:2,s:3,s:4");
      session.setAttribute("symbols", getSymbols());
    } else if (symbols == null && session.getAttribute("symbols") != null) {
      setSymbols((String) session.getAttribute("symbols"));
    }

    else {
      session.setAttribute("symbols", getSymbols());
    }

    java.util.StringTokenizer st = new java.util.StringTokenizer(symbols, " ,");
    QuoteData[] quoteDatas = new QuoteData[st.countTokens()];
    int count = 0;

    while (st.hasMoreElements()) {
      String symbol = st.nextToken();

      try {
        QuoteDataBean quoteData = tradeAction.getQuote(symbol);
        quoteDatas[count] = new QuoteData(quoteData.getOpen(), quoteData.getPrice(), quoteData.getSymbol(), quoteData.getHigh(), quoteData.getLow(),
            quoteData.getCompanyName(), quoteData.getVolume(), quoteData.getChange());
        count++;
      } catch (Exception e) {
        Log.error(e.toString());
      }
    }
    setQuotes(quoteDatas);
    return "quotes";
  }

  public String buy() {
    HttpSession session = (HttpSession) context.getSession(true);
    String userID = (String) session.getAttribute("uidBean");
    QuoteData quoteData = (QuoteData) dataTable.getRowData();
    OrderDataBean orderDataBean;

    try {
      orderDataBean = tradeAction.buy(userID, quoteData.getSymbol(), new Double(this.quantity).doubleValue(), TradeConfig.getOrderProcessingMode());

      OrderData orderData = new OrderData(orderDataBean.getOrderID(), orderDataBean.getOrderStatus(), orderDataBean.getOpenDate(),
          orderDataBean.getCompletionDate(), orderDataBean.getOrderFee(), orderDataBean.getOrderType(), orderDataBean.getQuantity(),
          orderDataBean.getSymbol());
      session.setAttribute("orderData", orderData);
    } catch (Exception e) {
      Log.error(e.toString());
      e.printStackTrace();
    }
    return "buy";
  }

  public void setQuotes(QuoteData[] quotes) {
    this.quotes = quotes;
  }

  public QuoteData[] getQuotes() {
    return quotes;
  }

  public void setSymbols(String symbols) {
    this.symbols = symbols;
  }

  public String getSymbols() {
    return symbols;
  }

  public void setDataTable(HtmlDataTable dataTable) {
    this.dataTable = dataTable;
  }

  public HtmlDataTable getDataTable() {
    return dataTable;
  }

  public void setQuantity(Integer quantity) {
    this.quantity = quantity;
  }

  public Integer getQuantity() {
    return quantity;
  }
}
