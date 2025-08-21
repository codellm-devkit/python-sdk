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
import java.util.Date;
import java.util.Iterator;

import javax.annotation.PostConstruct;
import javax.enterprise.context.RequestScoped;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.faces.context.ExternalContext;
import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.http.HttpSession;
import javax.validation.constraints.PastOrPresent;
import javax.validation.constraints.PositiveOrZero;

import com.ibm.websphere.samples.daytrader.entities.AccountDataBean;
import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;
import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.FinancialUtils;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Named("accountdata")
@RequestScoped
@Trace
public class AccountDataJSF {
  
  @Inject
  private ExternalContext context;

  private TradeServices tradeAction;

  private Date sessionCreationDate;
  private Date currentTime;
  private String profileID;
  private Integer accountID;
  
  @PastOrPresent
  private Date creationDate;
  
  @PositiveOrZero
  private int loginCount;
  
  @PastOrPresent
  private Date lastLogin;
  
  @PositiveOrZero
  private int logoutCount;
  private BigDecimal balance;
  private BigDecimal openBalance;
  private Integer numberHoldings;
  private BigDecimal holdingsTotal;
  private BigDecimal sumOfCashHoldings;
  private BigDecimal gain;
  private BigDecimal gainPercent;

  private OrderData[] closedOrders;
  private OrderData[] allOrders;  

  private Integer numberOfOrders = 0;
  private Integer numberOfOrderRows = 5;

  public void toggleShowAllRows() {
    setNumberOfOrderRows(0);
  }

  @Inject 
  public AccountDataJSF(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  @PostConstruct
  public void home() {
    try {
      HttpSession session = (HttpSession) context.getSession(true);

      // Get the data and then parse
      String userID = (String) session.getAttribute("uidBean");
      AccountDataBean accountData = tradeAction.getAccountData(userID);
      Collection<HoldingDataBean> holdingDataBeans = tradeAction.getHoldings(userID); 

      if (TradeConfig.getDisplayOrderAlerts()) {

        Collection<?> closedOrders = tradeAction.getClosedOrders(userID);

        if (closedOrders != null && closedOrders.size() > 0) {
          session.setAttribute("closedOrders", closedOrders);
          OrderData[] orderjsfs = new OrderData[closedOrders.size()];
          Iterator<?> it = closedOrders.iterator();
          int i = 0;

          while (it.hasNext()) {
            OrderDataBean order = (OrderDataBean) it.next();
            OrderData r = new OrderData(order.getOrderID(), order.getOrderStatus(), order.getOpenDate(), order.getCompletionDate(),
                order.getOrderFee(), order.getOrderType(), order.getQuantity(), order.getSymbol());
            orderjsfs[i] = r;
            i++;
          }

          setClosedOrders(orderjsfs);
        }
      }

      Collection<?> orderDataBeans = (TradeConfig.getLongRun() ? new ArrayList<Object>() : (Collection<?>) tradeAction.getOrders(userID));

      if (orderDataBeans != null && orderDataBeans.size() > 0) {
        session.setAttribute("orderDataBeans", orderDataBeans);
        OrderData[] orderjsfs = new OrderData[orderDataBeans.size()];
        Iterator<?> it = orderDataBeans.iterator();
        int i = 0;

        while (it.hasNext()) {
          OrderDataBean order = (OrderDataBean) it.next();
          OrderData r = new OrderData(order.getOrderID(), order.getOrderStatus(), order.getOpenDate(), order.getCompletionDate(),
              order.getOrderFee(), order.getOrderType(), order.getQuantity(), order.getSymbol(),order.getPrice());
          orderjsfs[i] = r;
          i++;
        }
        setNumberOfOrders(orderDataBeans.size());
        setAllOrders(orderjsfs);
      }

      setSessionCreationDate((Date) session.getAttribute("sessionCreationDate"));
      setCurrentTime(new java.util.Date());
      doAccountData(accountData, holdingDataBeans);
    } catch (Exception e) {
      e.printStackTrace();
    }
  }

  private void doAccountData(AccountDataBean accountData, Collection<HoldingDataBean> holdingDataBeans) {
    setProfileID(accountData.getProfileID());
    setAccountID(accountData.getAccountID());
    setCreationDate(accountData.getCreationDate());
    setLoginCount(accountData.getLoginCount());
    setLogoutCount(accountData.getLogoutCount());
    setLastLogin(accountData.getLastLogin());
    setOpenBalance(accountData.getOpenBalance());
    setBalance(accountData.getBalance());
    setNumberHoldings(holdingDataBeans.size());
    setHoldingsTotal(FinancialUtils.computeHoldingsTotal(holdingDataBeans));
    setSumOfCashHoldings(balance.add(holdingsTotal));
    setGain(FinancialUtils.computeGain(sumOfCashHoldings, openBalance));
    setGainPercent(FinancialUtils.computeGainPercent(sumOfCashHoldings, openBalance));
  }

  public Date getSessionCreationDate() {
    return sessionCreationDate;
  }

  public void setSessionCreationDate(Date sessionCreationDate) {
    this.sessionCreationDate = sessionCreationDate;
  }

  public Date getCurrentTime() {
    return currentTime;
  }

  public void setCurrentTime(Date currentTime) {
    this.currentTime = currentTime;
  }

  public String getProfileID() {
    return profileID;
  }

  public void setProfileID(String profileID) {
    this.profileID = profileID;
  }

  public void setAccountID(Integer accountID) {
    this.accountID = accountID;
  }

  public Integer getAccountID() {
    return accountID;
  }

  public void setCreationDate(Date creationDate) {
    this.creationDate = creationDate;
  }

  public Date getCreationDate() {
    return creationDate;
  }

  public void setLoginCount(int loginCount) {
    this.loginCount = loginCount;
  }

  public int getLoginCount() {
    return loginCount;
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

  public void setGain(BigDecimal gain) {
    this.gain = gain;
  }

  public BigDecimal getGain() {
    return gain;
  }

  public void setGainPercent(BigDecimal gainPercent) {
    this.gainPercent = gainPercent.setScale(2);
  }

  public BigDecimal getGainPercent() {
    return gainPercent;
  }

  public void setNumberHoldings(Integer numberHoldings) {
    this.numberHoldings = numberHoldings;
  }

  public Integer getNumberHoldings() {
    return numberHoldings;
  }

  public OrderData[] getClosedOrders() {
    return closedOrders;
  }

  public void setClosedOrders(OrderData[] closedOrders) {
    this.closedOrders = closedOrders;
  }

  public void setLastLogin(Date lastLogin) {
    this.lastLogin = lastLogin;
  }

  public Date getLastLogin() {
    return lastLogin;
  }

  public void setLogoutCount(int logoutCount) {
    this.logoutCount = logoutCount;
  }

  public int getLogoutCount() {
    return logoutCount;
  }

  public void setAllOrders(OrderData[] allOrders) {
    this.allOrders = allOrders;
  }

  public OrderData[] getAllOrders() {
    return allOrders;
  }

  public String getGainHTML() {
    return FinancialUtils.printGainHTML(gain);
  }

  public String getGainPercentHTML() {
    return FinancialUtils.printGainPercentHTML(gainPercent);
  }

  public Integer getNumberOfOrderRows() {
    return numberOfOrderRows;
  }

  public void setNumberOfOrderRows(Integer numberOfOrderRows) {
    this.numberOfOrderRows = numberOfOrderRows;
  }

  public Integer getNumberOfOrders() {
    return numberOfOrders;
  }

  public void setNumberOfOrders(Integer numberOfOrders) {
    this.numberOfOrders = numberOfOrders;
  }
}
