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
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;

import javax.annotation.PostConstruct;
import javax.enterprise.context.RequestScoped;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.faces.component.html.HtmlDataTable;
import javax.faces.context.ExternalContext;
import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.http.HttpSession;

import javax.validation.constraints.PositiveOrZero;

import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;
import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.FinancialUtils;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Named("portfolio")
@RequestScoped
@Trace
public class PortfolioJSF {
  @Inject
  private ExternalContext context;

  private TradeServices tradeAction;

  private BigDecimal balance;
  private BigDecimal openBalance;
  
  @PositiveOrZero
  private Integer numberHoldings;
  
  private BigDecimal holdingsTotal;
  private BigDecimal sumOfCashHoldings;
  private BigDecimal totalGain = new BigDecimal(0.0);
  private BigDecimal totalValue = new BigDecimal(0.0);
  private BigDecimal totalBasis = new BigDecimal(0.0);
  private BigDecimal totalGainPercent = new BigDecimal(0.0);
  private ArrayList<HoldingData> holdingDatas;
  private HtmlDataTable dataTable;

  @Inject 
  public PortfolioJSF(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  @PostConstruct
  public void getPortfolio() {            
    try {

      HttpSession session = (HttpSession) context.getSession(true);
      String userID = (String) session.getAttribute("uidBean");
      Collection<?> holdingDataBeans = tradeAction.getHoldings(userID);

      numberHoldings = holdingDataBeans.size();

      // Walk through the collection of user holdings and creating a list
      // of quotes
      if (holdingDataBeans.size() > 0) {
        Iterator<?> it = holdingDataBeans.iterator();
        holdingDatas = new ArrayList<HoldingData>(holdingDataBeans.size());

        while (it.hasNext()) {
          HoldingDataBean holdingData = (HoldingDataBean) it.next();
          QuoteDataBean quoteData = tradeAction.getQuote(holdingData.getQuoteID());

          BigDecimal basis = holdingData.getPurchasePrice().multiply(new BigDecimal(holdingData.getQuantity()));
          BigDecimal marketValue = quoteData.getPrice().multiply(new BigDecimal(holdingData.getQuantity()));
          totalBasis = totalBasis.add(basis);
          totalValue = totalValue.add(marketValue);
          BigDecimal gain = marketValue.subtract(basis);
          totalGain = totalGain.add(gain);

          HoldingData h = new HoldingData();
          h.setHoldingID(holdingData.getHoldingID());
          h.setPurchaseDate(holdingData.getPurchaseDate());
          h.setQuoteID(holdingData.getQuoteID());
          h.setQuantity(holdingData.getQuantity());
          h.setPurchasePrice(holdingData.getPurchasePrice());
          h.setBasis(basis);
          h.setGain(gain);
          h.setMarketValue(marketValue);
          h.setPrice(quoteData.getPrice());
          holdingDatas.add(h);

        }
        // dataTable
        setTotalGainPercent(FinancialUtils.computeGainPercent(totalValue, totalBasis));

      }
    } catch (Exception e) {
      e.printStackTrace();
    }
  }

  public String sell() {

    HttpSession session = (HttpSession) context.getSession(true);
    String userID = (String) session.getAttribute("uidBean");

    OrderDataBean orderDataBean = null;
    HoldingData holdingData = (HoldingData) dataTable.getRowData();

    try {
      orderDataBean = tradeAction.sell(userID, holdingData.getHoldingID(), TradeConfig.getOrderProcessingMode());
      holdingDatas.remove(holdingData);
    } catch (Exception e) {
      e.printStackTrace();
    }

    OrderData orderData = new OrderData(orderDataBean.getOrderID(), orderDataBean.getOrderStatus(), orderDataBean.getOpenDate(),
        orderDataBean.getCompletionDate(), orderDataBean.getOrderFee(), orderDataBean.getOrderType(), orderDataBean.getQuantity(),
        orderDataBean.getSymbol());
    session.setAttribute("orderData", orderData);
    return "sell";
  }

  public void setDataTable(HtmlDataTable dataTable) {
    this.dataTable = dataTable;
  }

  public HtmlDataTable getDataTable() {
    return dataTable;
  }

  public void setBalance(BigDecimal balance) {
    this.balance = balance;
  }

  public BigDecimal getBalance() {
    return balance;
  }

  public void setOpenBalance(BigDecimal openBalance) {
    this.openBalance = openBalance;
  }

  public BigDecimal getOpenBalance() {
    return openBalance;
  }

  public void setHoldingsTotal(BigDecimal holdingsTotal) {
    this.holdingsTotal = holdingsTotal;
  }

  public BigDecimal getHoldingsTotal() {
    return holdingsTotal;
  }

  public void setSumOfCashHoldings(BigDecimal sumOfCashHoldings) {
    this.sumOfCashHoldings = sumOfCashHoldings;
  }

  public BigDecimal getSumOfCashHoldings() {
    return sumOfCashHoldings;
  }

  public void setNumberHoldings(Integer numberHoldings) {
    this.numberHoldings = numberHoldings;
  }

  public Integer getNumberHoldings() {
    return numberHoldings;
  }

  public void setTotalGain(BigDecimal totalGain) {
    this.totalGain = totalGain;
  }

  public BigDecimal getTotalGain() {
    return totalGain;
  }

  public void setTotalValue(BigDecimal totalValue) {
    this.totalValue = totalValue;
  }

  public BigDecimal getTotalValue() {
    return totalValue;
  }

  public void setTotalBasis(BigDecimal totalBasis) {
    this.totalBasis = totalBasis;
  }

  public BigDecimal getTotalBasis() {
    return totalBasis;
  }

  public void setHoldingDatas(ArrayList<HoldingData> holdingDatas) {
    this.holdingDatas = holdingDatas;
  }

  public ArrayList<HoldingData> getHoldingDatas() {
    return holdingDatas;
  }

  public void setTotalGainPercent(BigDecimal totalGainPercent) {
    this.totalGainPercent = totalGainPercent;
  }

  public BigDecimal getTotalGainPercent() {
    return totalGainPercent;
  }

  public String getTotalGainPercentHTML() {
    return FinancialUtils.printGainPercentHTML(totalGainPercent);
  }
}
