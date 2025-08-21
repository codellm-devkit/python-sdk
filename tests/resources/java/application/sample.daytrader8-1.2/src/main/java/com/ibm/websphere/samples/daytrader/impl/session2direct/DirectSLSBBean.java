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
package com.ibm.websphere.samples.daytrader.impl.session2direct;

import java.math.BigDecimal;
import java.util.Collection;
import java.util.concurrent.Future;

import javax.ejb.Stateless;
import javax.ejb.TransactionAttribute;
import javax.ejb.TransactionAttributeType;
import javax.ejb.TransactionManagement;
import javax.ejb.TransactionManagementType;
import javax.inject.Inject;
import javax.validation.constraints.NotNull;

import com.ibm.websphere.samples.daytrader.interfaces.RuntimeMode;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeJDBC;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.interfaces.TradeSession2Direct;
import com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountProfileDataBean;
import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;
import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.impl.ejb3.AsyncScheduledOrderSubmitter;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

@Stateless
@TradeSession2Direct
@RuntimeMode("Session to Direct")
@Trace
@TransactionAttribute(TransactionAttributeType.REQUIRED)
@TransactionManagement(TransactionManagementType.CONTAINER)
public class DirectSLSBBean implements TradeServices {

  @Inject
  @TradeJDBC
  TradeServices tradeDirect;
  
  @Inject 
  AsyncScheduledOrderSubmitter asyncEJBOrderSubmitter; 

  @Override
  public int getImpl() {
    return TradeConfig.SESSION_TO_DIRECT;
  }

  @Override
  public MarketSummaryDataBean getMarketSummary() throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getMarketSummary();
  }

  @Override
  public OrderDataBean createOrder(AccountDataBean account, QuoteDataBean quote, HoldingDataBean holding,
      String orderType, double quantity) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.createOrder(account, quote, holding, orderType, quantity);
  }

  @Override
  @NotNull
  public OrderDataBean buy(String userID, String symbol, double quantity, int orderProcessingMode) throws Exception {
    tradeDirect.setInSession(true);
     OrderDataBean orderdata = tradeDirect.buy(userID, symbol, quantity, orderProcessingMode);
    
    if (orderProcessingMode == TradeConfig.ASYNCH) {
      this.completeOrderAsync(orderdata.getOrderID(), false);
    }
    
    return orderdata; 
  }

  @Override
  @NotNull
  public OrderDataBean sell(String userID, Integer holdingID, int orderProcessingMode) throws Exception {
    tradeDirect.setInSession(true);
    OrderDataBean orderdata =  tradeDirect.sell(userID, holdingID, orderProcessingMode);
    
    if (orderProcessingMode == TradeConfig.ASYNCH) {
      this.completeOrderAsync(orderdata.getOrderID(), false);
    }
    return orderdata;
    
  }

  @Override
  public void queueOrder(Integer orderID, boolean twoPhase) throws Exception {
    tradeDirect.setInSession(true);
    tradeDirect.queueOrder(orderID, twoPhase);

  }

  @Override
  public OrderDataBean completeOrder(Integer orderID, boolean twoPhase) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.completeOrder(orderID, twoPhase);
  }

  @Override
  public Future<OrderDataBean> completeOrderAsync(Integer orderID, boolean twoPhase) throws Exception {
    asyncEJBOrderSubmitter.submitOrder(orderID, twoPhase);
    return null;
  }

  @Override
  public void cancelOrder(Integer orderID, boolean twoPhase) throws Exception {
    tradeDirect.setInSession(true);
    tradeDirect.cancelOrder(orderID, twoPhase);

  }

  @Override
  public void orderCompleted(String userID, Integer orderID) throws Exception {
    tradeDirect.setInSession(true);
    tradeDirect.orderCompleted(userID, orderID);

  }

  @Override
  public Collection<?> getOrders(String userID) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getOrders(userID);
  }

  @Override
  public Collection<?> getClosedOrders(String userID) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getClosedOrders(userID);
  }

  @Override
  public QuoteDataBean createQuote(String symbol, String companyName, BigDecimal price) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.createQuote(symbol, companyName, price);
  }

  @Override
  public QuoteDataBean getQuote(String symbol) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getQuote(symbol); 
  }

  @Override
  public Collection<?> getAllQuotes() throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getAllQuotes();
  }

  @Override
  public QuoteDataBean updateQuotePriceVolume(String symbol, BigDecimal newPrice, double sharesTraded)
      throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.updateQuotePriceVolume(symbol, newPrice, sharesTraded);
  }

  @Override
  public Collection<HoldingDataBean> getHoldings(String userID) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getHoldings(userID);
  }

  @Override
  public HoldingDataBean getHolding(Integer holdingID) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getHolding(holdingID);
  }

  @Override
  public AccountDataBean getAccountData(String userID) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getAccountData(userID);
  }

  @Override
  public AccountProfileDataBean getAccountProfileData(String userID) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.getAccountProfileData(userID);
  }

  @Override
  public AccountProfileDataBean updateAccountProfile(AccountProfileDataBean profileData) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.updateAccountProfile(profileData);
  }

  @Override
  public AccountDataBean login(String userID, String password) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.login(userID, password);
  }

  @Override
  public void logout(String userID) throws Exception {
    tradeDirect.setInSession(true);
    tradeDirect.logout(userID);
  }

  @Override
  public AccountDataBean register(String userID, String password, String fullname, String address, String email,
      String creditcard, BigDecimal openBalance) throws Exception {
    tradeDirect.setInSession(true);
    return tradeDirect.register(userID, password, fullname, address, email, creditcard, openBalance);
  }

  @Override
  public QuoteDataBean pingTwoPhase(String symbol) throws Exception {
    throw new UnsupportedOperationException();
  }

  @Override
  public double investmentReturn(double rnd1, double rnd2) throws Exception {
    throw new UnsupportedOperationException();
  }


  @Override
  public void setInSession(boolean inSession) {
    throw new UnsupportedOperationException("DirectSLSBBean::setInGlobalTxn not supported");
  }
}