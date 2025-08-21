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

import javax.annotation.PostConstruct;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.faces.context.ExternalContext;
import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Named("orderdata")
@Trace
public class OrderDataJSF {
  
  @Inject
  private ExternalContext context;

  private TradeServices tradeAction;

  private OrderData[] allOrders;
  private OrderData orderData;

  @Inject 
  public OrderDataJSF(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  public void getAllOrder() {
    try {
      HttpSession session = (HttpSession) context.getSession(true);
      String userID = (String) session.getAttribute("uidBean");

      ArrayList<?> orderDataBeans = (TradeConfig.getLongRun() ? new ArrayList<Object>() : (ArrayList<?>) tradeAction.getOrders(userID));
      OrderData[] orders = new OrderData[orderDataBeans.size()];

      int count = 0;

      for (Object order : orderDataBeans) {
        OrderData r = new OrderData(((OrderDataBean) order).getOrderID(), ((OrderDataBean) order).getOrderStatus(),
            ((OrderDataBean) order).getOpenDate(), ((OrderDataBean) order).getCompletionDate(), ((OrderDataBean) order).getOrderFee(),
            ((OrderDataBean) order).getOrderType(), ((OrderDataBean) order).getQuantity(), ((OrderDataBean) order).getSymbol());
        r.setPrice(((OrderDataBean) order).getPrice());
        r.setTotal(r.getPrice().multiply(new BigDecimal(r.getQuantity())));
        orders[count] = r;
        count++;
      }

      setAllOrders(orders);
    } catch (Exception e) {
      e.printStackTrace();
    }

  }

  @PostConstruct
  public void getOrder() {


    HttpSession session = (HttpSession) context.getSession(true);
    OrderData order = (OrderData) session.getAttribute("orderData");

    if (order != null) {
      setOrderData(order);
    }
  }

  public void setAllOrders(OrderData[] allOrders) {
    this.allOrders = allOrders;
  }

  public OrderData[] getAllOrders() {
    return allOrders;
  }

  public void setOrderData(OrderData orderData) {
    this.orderData = orderData;
  }

  public OrderData getOrderData() {
    return orderData;
  }
}
