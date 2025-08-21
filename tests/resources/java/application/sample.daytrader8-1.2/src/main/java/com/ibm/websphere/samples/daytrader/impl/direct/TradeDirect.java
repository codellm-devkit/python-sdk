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
package com.ibm.websphere.samples.daytrader.impl.direct;

import java.io.Serializable;
import java.math.BigDecimal;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Collection;
import java.util.concurrent.Future;

import javax.annotation.Resource;
import javax.enterprise.concurrent.ManagedExecutorService;
import javax.enterprise.context.Dependent;
import javax.enterprise.event.Event;
import javax.enterprise.event.NotificationOptions;
import javax.inject.Inject;
import javax.jms.JMSContext;
import javax.jms.JMSException;
import javax.jms.Queue;
import javax.jms.QueueConnectionFactory;
import javax.jms.TextMessage;
import javax.jms.Topic;
import javax.jms.TopicConnectionFactory;
import javax.sql.DataSource;
import javax.transaction.UserTransaction;
import javax.validation.constraints.NotNull;

import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountProfileDataBean;
import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;
import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.util.FinancialUtils;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.MDBStats;
import com.ibm.websphere.samples.daytrader.util.RecentQuotePriceChangeList;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.interfaces.MarketSummaryUpdate;
import com.ibm.websphere.samples.daytrader.interfaces.RuntimeMode;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeJDBC;

/**
 * TradeDirect uses direct JDBC and JMS access to a
 * <code>javax.sql.DataSource</code> to implement the business methods of the
 * Trade online broker application. These business methods represent the
 * features and operations that can be performed by customers of the brokerage
 * such as login, logout, get a stock quote, buy or sell a stock, etc. and are
 * specified in the {@link com.ibm.websphere.samples.daytrader.TradeServices}
 * interface
 *
 * Note: In order for this class to be thread-safe, a new TradeJDBC must be
 * created for each call to a method from the TradeInterface interface.
 * Otherwise, pooled connections may not be released.
 *
 * @see com.ibm.websphere.samples.daytrader.TradeServices
 *
 */

@Dependent
@TradeJDBC
@RuntimeMode("Direct (JDBC)")
@Trace
public class TradeDirect implements TradeServices, Serializable {
  /**
   * 
   */
  private static final long serialVersionUID = -8089049090952927985L;

  //This lock is used to serialize market summary operations.
  private static final Integer marketSummaryLock = new Integer(0);
  private static long nextMarketSummary = System.currentTimeMillis();
  private static MarketSummaryDataBean cachedMSDB = MarketSummaryDataBean.getRandomInstance();

  private static BigDecimal ZERO = new BigDecimal(0.0);
  private boolean inGlobalTxn = false;
  private boolean inSession = false;

  // For Wildfly - add java:/ to these resource names.
  
  @Resource(name = "jms/QueueConnectionFactory", authenticationType = javax.annotation.Resource.AuthenticationType.APPLICATION)
  private QueueConnectionFactory queueConnectionFactory;

  @Resource(name = "jms/TopicConnectionFactory", authenticationType = javax.annotation.Resource.AuthenticationType.APPLICATION)
  private TopicConnectionFactory topicConnectionFactory;

  @Resource(lookup = "jms/TradeStreamerTopic")
  private Topic tradeStreamerTopic;

  @Resource(lookup = "jms/TradeBrokerQueue")
  private Queue tradeBrokerQueue;

  @Resource(lookup = "jdbc/TradeDataSource")
  private DataSource datasource;

  @Resource
  private  UserTransaction txn;

  @Inject
  RecentQuotePriceChangeList recentQuotePriceChangeList;
  
  @Inject 
  AsyncOrderSubmitter asyncOrderSubmitter;  
  
  @Inject
  @MarketSummaryUpdate
  Event<String> mkSummaryUpdateEvent;
  
  @Resource
  private ManagedExecutorService mes;
  

  @Override
  public MarketSummaryDataBean getMarketSummary() throws Exception {

    if (TradeConfig.getMarketSummaryInterval() == 0) {
      return getMarketSummaryInternal();
    }
    if (TradeConfig.getMarketSummaryInterval() < 0) {
      return cachedMSDB;
    }

    /**
     * This is a little funky. If its time to fetch a new Market summary
     * then we'll synchronize access to make sure only one requester does
     * it. Others will merely return the old copy until the new
     * MarketSummary has been executed.
     */

    long currentTime = System.currentTimeMillis();

    if (currentTime > nextMarketSummary) {
      long oldNextMarketSummary = nextMarketSummary;
      boolean fetch = false;

      synchronized (marketSummaryLock) {
        /**
         * Is it still ahead or did we miss lose the race? If we lost
         * then let's get out of here as the work has already been done.
         */
        if (oldNextMarketSummary == nextMarketSummary) {
          fetch = true;
          nextMarketSummary += TradeConfig.getMarketSummaryInterval() * 1000;

          /**
           * If the server has been idle for a while then its possible
           * that nextMarketSummary could be way off. Rather than try
           * and play catch up we'll simply get in sync with the
           * current time + the interval.
           */
          if (nextMarketSummary < currentTime) {
            nextMarketSummary = currentTime + TradeConfig.getMarketSummaryInterval() * 1000;
          }
        }
      }

      /**
       * If we're the lucky one then let's update the MarketSummary
       */
      if (fetch) {
        cachedMSDB = getMarketSummaryInternal();
  
      }
    }

    return cachedMSDB;
  }



  /**
   * @see TradeServices#getMarketSummary()
   */

  public MarketSummaryDataBean getMarketSummaryInternal() throws Exception {

    MarketSummaryDataBean marketSummaryData = null;
    Connection conn = null;
    try {

      Log.trace("TradeDirect:getMarketSummary - inSession(" + this.inSession + ")");

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, getTSIAQuotesOrderByChangeSQL, ResultSet.TYPE_SCROLL_INSENSITIVE, ResultSet.CONCUR_READ_ONLY);

      ArrayList<QuoteDataBean> topGainersData = new ArrayList<QuoteDataBean>(5);
      ArrayList<QuoteDataBean> topLosersData = new ArrayList<QuoteDataBean>(5);

      ResultSet rs = stmt.executeQuery();

      int count = 0;
      while (rs.next() && (count++ < 5)) {
        QuoteDataBean quoteData = getQuoteDataFromResultSet(rs);
        topLosersData.add(quoteData);
      }

      stmt.close();
      stmt = getStatement(conn, "select * from quoteejb q order by q.change1 DESC", ResultSet.TYPE_SCROLL_INSENSITIVE, ResultSet.CONCUR_READ_ONLY);
      rs = stmt.executeQuery();

      count = 0;
      while (rs.next() && (count++ < 5)) {
        QuoteDataBean quoteData = getQuoteDataFromResultSet(rs);
        topGainersData.add(quoteData);
      }

      /*
       * rs.last(); count = 0; while (rs.previous() && (count++ < 5) ) {
       * QuoteDataBean quoteData = getQuoteDataFromResultSet(rs);
       * topGainersData.add(quoteData); }
       */

      stmt.close();

      BigDecimal TSIA = ZERO;
      BigDecimal openTSIA = ZERO;
      double volume = 0.0;

      if ((topGainersData.size() > 0) || (topLosersData.size() > 0)) {

        stmt = getStatement(conn, getTSIASQL);
        rs = stmt.executeQuery();

        if (!rs.next()) {
          Log.error("TradeDirect:getMarketSummary -- error w/ getTSIASQL -- no results");
        } else {
          TSIA = rs.getBigDecimal("TSIA");
        }
        stmt.close();

        stmt = getStatement(conn, getOpenTSIASQL);
        rs = stmt.executeQuery();

        if (!rs.next()) {
          Log.error("TradeDirect:getMarketSummary -- error w/ getOpenTSIASQL -- no results");
        } else {
          openTSIA = rs.getBigDecimal("openTSIA");
        }
        stmt.close();

        stmt = getStatement(conn, getTSIATotalVolumeSQL);
        rs = stmt.executeQuery();

        if (!rs.next()) {
          Log.error("TradeDirect:getMarketSummary -- error w/ getTSIATotalVolumeSQL -- no results");
        } else {
          volume = rs.getDouble("totalVolume");
        }
        stmt.close();
      }
      commit(conn);

      marketSummaryData = new MarketSummaryDataBean(TSIA, openTSIA, volume, topGainersData, topLosersData);
      mkSummaryUpdateEvent.fireAsync("MarketSummaryUpdate", NotificationOptions.builder().setExecutor(mes).build());

    }

    catch (Exception e) {
      Log.error("TradeDirect:login -- error logging in user", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return marketSummaryData;

  }

  /**
   * @see TradeServices#buy(String, String, double)
   */
  @Override
  @NotNull
  public OrderDataBean buy(String userID, String symbol, double quantity, int orderProcessingMode) throws Exception {

    final Connection conn = getConn();
    OrderDataBean orderData = null;

    BigDecimal total;

    try {

      Log.trace("TradeDirect:buy - inSession(" + this.inSession + ")");//, userID, symbol, new Double(quantity));


      if (!inSession && orderProcessingMode == TradeConfig.ASYNCH_2PHASE) {

        Log.trace("TradeDirect:buy create/begin global transaction");


        txn.begin();
        setInGlobalTxn(true);
      }

      //conn = getConn();

      AccountDataBean accountData = getAccountData(conn, userID);
      QuoteDataBean quoteData = getQuoteData(conn, symbol);
      HoldingDataBean holdingData = null; // the buy operation will create
      // the holding

      orderData = createOrder(accountData, quoteData, holdingData, "buy", quantity);

      // Update -- account should be credited during completeOrder
      BigDecimal price = quoteData.getPrice();
      BigDecimal orderFee = orderData.getOrderFee();
      total = (new BigDecimal(quantity).multiply(price)).add(orderFee);
      // subtract total from account balance
      creditAccountBalance(conn, accountData, total.negate());
      final Integer orderID = orderData.getOrderID();

      try {

        if (orderProcessingMode == TradeConfig.SYNCH) {
          completeOrder(conn, orderData.getOrderID());
        } else if (orderProcessingMode == TradeConfig.ASYNCH) {
          completeOrderAsync(orderID, true);
        } else  if (orderProcessingMode == TradeConfig.ASYNCH_2PHASE) {
          queueOrder(orderID, true); // 2-phase
        } 
      } catch (JMSException je) {
        Log.error("TradeBean:buy(" + userID + "," + symbol + "," + quantity + ") --> failed to queueOrder", je);


        cancelOrder(conn, orderData.getOrderID());
      }

      orderData = getOrderData(conn, orderData.getOrderID().intValue());

      if (getInGlobalTxn()) {
        
        Log.trace("TradeDirect:buy committing global transaction");
        
        if (!inSession && orderProcessingMode == TradeConfig.ASYNCH_2PHASE) {
          txn.commit();
          setInGlobalTxn(false);
        }
      } else {
        commit(conn);
      }
    } catch (Exception e) {
      Log.error("TradeDirect:buy error - rolling back", e);
      if (getInGlobalTxn()) {
        txn.rollback();
      } else {
        rollBack(conn, e);
      }
    } finally {
      releaseConn(conn);
    }

    return orderData;
  }

  /**
   * @see TradeServices#sell(String, Integer)
   */
  @Override
  @NotNull
  public OrderDataBean sell(String userID, Integer holdingID, int orderProcessingMode) throws Exception {
    Connection conn = null;
    OrderDataBean orderData = null;
    //UserTransaction txn = null;

    /*
     * total = (quantity * purchasePrice) + orderFee
     */
    BigDecimal total;

    try {
     
      Log.trace("TradeDirect:sell - inSession(" + this.inSession + ")", userID, holdingID);
      

      if (!inSession && orderProcessingMode == TradeConfig.ASYNCH_2PHASE) {
       
        Log.trace("TradeDirect:sell create/begin global transaction");
        
        txn.begin();
        setInGlobalTxn(true);
      }

      conn = getConn();

      AccountDataBean accountData = getAccountData(conn, userID);
      HoldingDataBean holdingData = getHoldingData(conn, holdingID.intValue());
      QuoteDataBean quoteData = null;
      if (holdingData != null) {
        quoteData = getQuoteData(conn, holdingData.getQuoteID());
      }

      if ((accountData == null) || (holdingData == null) || (quoteData == null)) {
        String error = "TradeDirect:sell -- error selling stock -- unable to find:  \n\taccount=" + accountData + "\n\tholding=" + holdingData
            + "\n\tquote=" + quoteData + "\nfor user: " + userID + " and holdingID: " + holdingID;
        Log.debug(error);
        if (getInGlobalTxn()) {
          txn.rollback();
        } else {
          rollBack(conn, new Exception(error));
        }
        orderData = new OrderDataBean();
        orderData.setOrderStatus("cancelled");
        return orderData;
      }

      double quantity = holdingData.getQuantity();

      orderData = createOrder(accountData, quoteData, holdingData, "sell", quantity);

      // Set the holdingSymbol purchaseDate to selling to signify the sell
      // is "inflight"
      updateHoldingStatus(conn, holdingData.getHoldingID(), holdingData.getQuoteID());

      // UPDATE -- account should be credited during completeOrder
      BigDecimal price = quoteData.getPrice();
      BigDecimal orderFee = orderData.getOrderFee();
      total = (new BigDecimal(quantity).multiply(price)).subtract(orderFee);
      creditAccountBalance(conn, accountData, total);

      try {
        if (orderProcessingMode == TradeConfig.SYNCH) {
          completeOrder(conn, orderData.getOrderID());
        } else if (orderProcessingMode == TradeConfig.ASYNCH) {
          this.completeOrderAsync(orderData.getOrderID(), true);
        } else if (orderProcessingMode == TradeConfig.ASYNCH_2PHASE) {
          queueOrder(orderData.getOrderID(), true);
        } 
      } catch (JMSException je) {
        Log.error("TradeBean:sell(" + userID + "," + holdingID + ") --> failed to queueOrder", je);

        cancelOrder(conn, orderData.getOrderID());
      }

      orderData = getOrderData(conn, orderData.getOrderID().intValue());

      if (!inSession && orderProcessingMode == TradeConfig.ASYNCH_2PHASE) {
       
        Log.trace("TradeDirect:sell committing global transaction");
        
        txn.commit();
        setInGlobalTxn(false);
      } else {
        commit(conn);
      }
    } catch (Exception e) {
      Log.error("TradeDirect:sell error", e);
      if (getInGlobalTxn()) {
        txn.rollback();
      } else {
        rollBack(conn, e);
      }
    } finally {
      releaseConn(conn);
    }

    return orderData;
  }

  /**
   * @see TradeServices#queueOrder(Integer)
   */
  @Override
  public void queueOrder(Integer orderID, boolean twoPhase) throws Exception {

   
    Log.trace("TradeDirect:queueOrder - inSession(" + this.inSession + ")", orderID);
    

    try (JMSContext context = queueConnectionFactory.createContext();){	
      TextMessage message = context.createTextMessage();

      message.setStringProperty("command", "neworder");
      message.setIntProperty("orderID", orderID.intValue());
      message.setBooleanProperty("twoPhase", twoPhase);
      message.setBooleanProperty("direct", true);
      message.setLongProperty("publishTime", System.currentTimeMillis());
      message.setText("neworder: orderID=" + orderID + " runtimeMode=Direct twoPhase=" + twoPhase);

      context.createProducer().send(tradeBrokerQueue, message);
    } catch (Exception e) {            
      throw e; // pass the exception
    }
  }

  /**
   * @see TradeServices#completeOrder(Integer)
   */
  @Override
  public OrderDataBean completeOrder(Integer orderID, boolean twoPhase) throws Exception {
    OrderDataBean orderData = null;
    Connection conn = null;

    try { // twoPhase

    
      Log.trace("TradeDirect:completeOrder - inSession(" + this.inSession + ")", orderID);
      
      setInGlobalTxn(!inSession && twoPhase);
      conn = getConn();

      orderData = completeOrder(conn, orderID);

      commit(conn);

    } catch (Exception e) {
      Log.error("TradeDirect:completeOrder -- error completing order", e);
      rollBack(conn, e);
      cancelOrder(orderID, twoPhase);
    } finally {
      releaseConn(conn);
    }

    return orderData;

  }

  @Override
  public Future<OrderDataBean> completeOrderAsync(Integer orderID, boolean twoPhase) throws Exception {
    if (!inSession) {
      asyncOrderSubmitter.submitOrder(orderID, twoPhase);
    }
    return null;
  }


  private OrderDataBean completeOrder(Connection conn, Integer orderID) throws Exception {
    //conn = getConn();
    OrderDataBean orderData = null;
   
    Log.trace("TradeDirect:completeOrderInternal - inSession(" + this.inSession + ")", orderID);
    

    PreparedStatement stmt = getStatement(conn, getOrderSQL);
    stmt.setInt(1, orderID.intValue());

    ResultSet rs = stmt.executeQuery();

    if (!rs.next()) {
      Log.error("TradeDirect:completeOrder -- unable to find order: " + orderID);
      stmt.close();
      return orderData;
    }
    orderData = getOrderDataFromResultSet(rs);

    String orderType = orderData.getOrderType();
    String orderStatus = orderData.getOrderStatus();

    // if (order.isCompleted())
    if ((orderStatus.compareToIgnoreCase("completed") == 0) || (orderStatus.compareToIgnoreCase("alertcompleted") == 0)
        || (orderStatus.compareToIgnoreCase("cancelled") == 0)) {
      throw new Exception("TradeDirect:completeOrder -- attempt to complete Order that is already completed");
    }

    int accountID = rs.getInt("account_accountID");
    String quoteID = rs.getString("quote_symbol");
    int holdingID = rs.getInt("holding_holdingID");

    BigDecimal price = orderData.getPrice();
    double quantity = orderData.getQuantity();

    // get the data for the account and quote
    // the holding will be created for a buy or extracted for a sell

    /*
     * Use the AccountID and Quote Symbol from the Order AccountDataBean
     * accountData = getAccountData(accountID, conn); QuoteDataBean
     * quoteData = getQuoteData(conn, quoteID);
     */
    String userID = getAccountProfileData(conn, new Integer(accountID)).getUserID();

    HoldingDataBean holdingData = null;

   
    Log.trace("TradeDirect:completeOrder--> Completing Order " + orderData.getOrderID() + "\n\t Order info: " + orderData + "\n\t Account info: "
          + accountID + "\n\t Quote info: " + quoteID);
    

    // if (order.isBuy())
    if (orderType.compareToIgnoreCase("buy") == 0) {
      /*
       * Complete a Buy operation - create a new Holding for the Account -
       * deduct the Order cost from the Account balance
       */

      holdingData = createHolding(conn, accountID, quoteID, quantity, price);
      updateOrderHolding(conn, orderID.intValue(), holdingData.getHoldingID().intValue());
      updateOrderStatus(conn, orderData.getOrderID(), "closed");
      updateQuotePriceVolume(orderData.getSymbol(), TradeConfig.getRandomPriceChangeFactor(), orderData.getQuantity());
    }

    // if (order.isSell()) {
    if (orderType.compareToIgnoreCase("sell") == 0) {
      /*
       * Complete a Sell operation - remove the Holding from the Account -
       * deposit the Order proceeds to the Account balance
       */
      holdingData = getHoldingData(conn, holdingID);
      if (holdingData == null) {
        Log.debug("TradeDirect:completeOrder:sell -- user: " + userID + " already sold holding: " + holdingID);
        updateOrderStatus(conn, orderData.getOrderID(), "cancelled");
      } else {
        removeHolding(conn, holdingID, orderID.intValue());
        updateOrderStatus(conn, orderData.getOrderID(), "closed");
        updateQuotePriceVolume(orderData.getSymbol(), TradeConfig.getRandomPriceChangeFactor(), orderData.getQuantity());
      }

    } 



    Log.trace("TradeDirect:completeOrder--> Completed Order " + orderData.getOrderID() + "\n\t Order info: " + orderData + "\n\t Account info: "
          + accountID + "\n\t Quote info: " + quoteID + "\n\t Holding info: " + holdingData);
    
    stmt.close();

    commit(conn);



    return orderData;
  }

  /**
   * @see TradeServices#cancelOrder(Integer, boolean)
   */
  @Override
  public void cancelOrder(Integer orderID, boolean twoPhase) throws Exception {

    Connection conn = null;
    try {
     
      Log.trace("TradeDirect:cancelOrder - inSession(" + this.inSession + ")", orderID);
      
      setInGlobalTxn(!inSession && twoPhase);
      conn = getConn();
      cancelOrder(conn, orderID);
      commit(conn);

    } catch (Exception e) {
      Log.error("TradeDirect:cancelOrder -- error cancelling order: " + orderID, e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
  }

  private void cancelOrder(Connection conn, Integer orderID) throws Exception {
    updateOrderStatus(conn, orderID, "cancelled");
  }

  @Override
  public void orderCompleted(String userID, Integer orderID) throws Exception {
    throw new UnsupportedOperationException("TradeDirect:orderCompleted method not supported");
  }

  private HoldingDataBean createHolding(Connection conn, int accountID, String symbol, double quantity, BigDecimal purchasePrice) throws Exception {

    Timestamp purchaseDate = new Timestamp(System.currentTimeMillis());
    PreparedStatement stmt = getStatement(conn, createHoldingSQL);

    Integer holdingID = KeySequenceDirect.getNextID(conn, "holding", inSession, getInGlobalTxn());
    stmt.setInt(1, holdingID.intValue());
    stmt.setTimestamp(2, purchaseDate);
    stmt.setBigDecimal(3, purchasePrice);
    stmt.setDouble(4, quantity);
    stmt.setString(5, symbol);
    stmt.setInt(6, accountID);
    stmt.executeUpdate();

    stmt.close();

    return getHoldingData(conn, holdingID.intValue());
  }

  private void removeHolding(Connection conn, int holdingID, int orderID) throws Exception {
    PreparedStatement stmt = getStatement(conn, removeHoldingSQL);

    stmt.setInt(1, holdingID);
    stmt.executeUpdate();
    stmt.close();

    // set the HoldingID to NULL for the purchase and sell order now that
    // the holding as been removed
    stmt = getStatement(conn, removeHoldingFromOrderSQL);

    stmt.setInt(1, holdingID);
    stmt.executeUpdate();
    stmt.close();

  }

  public OrderDataBean createOrder(AccountDataBean accountData, QuoteDataBean quoteData, HoldingDataBean holdingData, String orderType,
      double quantity) throws Exception {
    OrderDataBean orderData = null;
    Connection conn = null;
    try {

      conn = getConn();
      Timestamp currentDate = new Timestamp(System.currentTimeMillis());

      PreparedStatement stmt = getStatement(conn, createOrderSQL);

      Integer orderID = KeySequenceDirect.getNextID(conn, "order", inSession, getInGlobalTxn());
      stmt.setInt(1, orderID.intValue());
      stmt.setString(2, orderType);
      stmt.setString(3, "open");
      stmt.setTimestamp(4, currentDate);
      stmt.setDouble(5, quantity);
      stmt.setBigDecimal(6, quoteData.getPrice().setScale(FinancialUtils.SCALE, FinancialUtils.ROUND));
      stmt.setBigDecimal(7, TradeConfig.getOrderFee(orderType));
      stmt.setInt(8, accountData.getAccountID().intValue());
      if (holdingData == null) {
        stmt.setNull(9, java.sql.Types.INTEGER);
      } else {
        stmt.setInt(9, holdingData.getHoldingID().intValue());
      }
      stmt.setString(10, quoteData.getSymbol());
      stmt.executeUpdate();

      orderData = getOrderData(conn, orderID.intValue());

      stmt.close();

      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:createOrder -- error getting user orders", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }

    return orderData;
  }

  /**
   * @see TradeServices#getOrders(String)
   */
  @Override
  public Collection<OrderDataBean> getOrders(String userID) throws Exception {
    Collection<OrderDataBean> orderDataBeans = new ArrayList<OrderDataBean>();
    Connection conn = null;
    try {
      Log.trace("TradeDirect:getOrders - inSession(" + this.inSession + ")", userID);
      

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, getOrdersByUserSQL);
      stmt.setString(1, userID);

      ResultSet rs = stmt.executeQuery();

      // TODO: return top 5 orders for now -- next version will add a
      // getAllOrders method
      // also need to get orders sorted by order id descending
      int i = 0;
      while ((rs.next()) && (i++ < 5)) {
        OrderDataBean orderData = getOrderDataFromResultSet(rs);
        orderDataBeans.add(orderData);
      }

      stmt.close();
      commit(conn);

    } catch (Exception e) {
      Log.error("TradeDirect:getOrders -- error getting user orders", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return orderDataBeans;
  }

  /**
   * @see TradeServices#getClosedOrders(String)
   */
  @Override
  public Collection<OrderDataBean> getClosedOrders(String userID) throws Exception {
    Collection<OrderDataBean> orderDataBeans = new ArrayList<OrderDataBean>();
    Connection conn = null;
    try {

      Log.trace("TradeDirect:getClosedOrders - inSession(" + this.inSession + ")", userID);
      

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, getClosedOrdersSQL);
      stmt.setString(1, userID);

      ResultSet rs = stmt.executeQuery();

      while (rs.next()) {
        OrderDataBean orderData = getOrderDataFromResultSet(rs);
        orderData.setOrderStatus("completed");
        updateOrderStatus(conn, orderData.getOrderID(), orderData.getOrderStatus());
        orderDataBeans.add(orderData);

      }

      stmt.close();
      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:getOrders -- error getting user orders", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return orderDataBeans;
  }

  /**
   * @see TradeServices#createQuote(String, String, BigDecimal)
   */
  @Override
  public QuoteDataBean createQuote(String symbol, String companyName, BigDecimal price) throws Exception {

    QuoteDataBean quoteData = null;
    Connection conn = null;
    try {

      Log.trace("TradeDirect:createQuote - inSession(" + this.inSession + ")");
      

      price = price.setScale(FinancialUtils.SCALE, FinancialUtils.ROUND);
      double volume = 0.0, change = 0.0;

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, createQuoteSQL);
      stmt.setString(1, symbol); // symbol
      stmt.setString(2, companyName); // companyName
      stmt.setDouble(3, volume); // volume
      stmt.setBigDecimal(4, price); // price
      stmt.setBigDecimal(5, price); // open
      stmt.setBigDecimal(6, price); // low
      stmt.setBigDecimal(7, price); // high
      stmt.setDouble(8, change); // change

      stmt.executeUpdate();
      stmt.close();
      commit(conn);

      quoteData = new QuoteDataBean(symbol, companyName, volume, price, price, price, price, change);
    } catch (Exception e) {
      Log.error("TradeDirect:createQuote -- error creating quote", e);
    } finally {
      releaseConn(conn);
    }
    return quoteData;
  }

  /**
   * @see TradeServices#getQuote(String)
   */

  @Override
  public QuoteDataBean getQuote(String symbol) throws Exception {
    QuoteDataBean quoteData = null;
    Connection conn = null;

    try {
     
      Log.trace("TradeDirect:getQuote - inSession(" + this.inSession + ")", symbol);
    

      conn = getConn();
      quoteData = getQuote(conn, symbol);
      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:getQuote -- error getting quote", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return quoteData;
  }

  private QuoteDataBean getQuote(Connection conn, String symbol) throws Exception {
    QuoteDataBean quoteData = null;
    PreparedStatement stmt = getStatement(conn, getQuoteSQL);
    stmt.setString(1, symbol); // symbol

    ResultSet rs = stmt.executeQuery();

    if (!rs.next()) {
      Log.error("TradeDirect:getQuote -- failure no result.next() for symbol: " + symbol);
    } else {
      quoteData = getQuoteDataFromResultSet(rs);
    }

    stmt.close();

    return quoteData;
  }

  private QuoteDataBean getQuoteForUpdate(Connection conn, String symbol) throws Exception {
    QuoteDataBean quoteData = null;
    PreparedStatement stmt = getStatement(conn, getQuoteForUpdateSQL);
    stmt.setString(1, symbol); // symbol

    ResultSet rs = stmt.executeQuery();

    if (!rs.next()) {
      Log.error("TradeDirect:getQuote -- failure no result.next()");
    } else {
      quoteData = getQuoteDataFromResultSet(rs);
    }

    stmt.close();

    return quoteData;
  }

  /**
   * @see TradeServices#getAllQuotes(String)
   */
  @Override
  public Collection<QuoteDataBean> getAllQuotes() throws Exception {
    Collection<QuoteDataBean> quotes = new ArrayList<QuoteDataBean>();
    QuoteDataBean quoteData = null;

    Connection conn = null;
    try {
      conn = getConn();

      PreparedStatement stmt = getStatement(conn, getAllQuotesSQL);

      ResultSet rs = stmt.executeQuery();

      while (!rs.next()) {
        quoteData = getQuoteDataFromResultSet(rs);
        quotes.add(quoteData);
      }

      stmt.close();
    } catch (Exception e) {
      Log.error("TradeDirect:getAllQuotes", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }

    return quotes;
  }

  /**
   * @see TradeServices#getHoldings(String)
   */
  @Override
  public Collection<HoldingDataBean> getHoldings(String userID) throws Exception {
    Collection<HoldingDataBean> holdingDataBeans = new ArrayList<HoldingDataBean>();
    Connection conn = null;
    try {
    
      Log.trace("TradeDirect:getHoldings - inSession(" + this.inSession + ")", userID);
      

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, getHoldingsForUserSQL);
      stmt.setString(1, userID);

      ResultSet rs = stmt.executeQuery();

      while (rs.next()) {
        HoldingDataBean holdingData = getHoldingDataFromResultSet(rs);
        holdingDataBeans.add(holdingData);
      }

      stmt.close();
      commit(conn);

    } catch (Exception e) {
      Log.error("TradeDirect:getHoldings -- error getting user holings", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return holdingDataBeans;
  }

  /**
   * @see TradeServices#getHolding(Integer)
   */
  @Override
  public HoldingDataBean getHolding(Integer holdingID) throws Exception {
    HoldingDataBean holdingData = null;
    Connection conn = null;
    try {

      Log.trace("TradeDirect:getHolding - inSession(" + this.inSession + ")", holdingID);
      

      conn = getConn();
      holdingData = getHoldingData(holdingID.intValue());

      commit(conn);

    } catch (Exception e) {
      Log.error("TradeDirect:getHolding -- error getting holding " + holdingID + "", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return holdingData;
  }

  /**
   * @see TradeServices#getAccountData(String)
   */
  @Override
  public AccountDataBean getAccountData(String userID) throws Exception {
    try {
      AccountDataBean accountData = null;
      Connection conn = null;
      try {

        Log.trace("TradeDirect:getAccountData - inSession(" + this.inSession + ")", userID);
        

        conn = getConn();
        accountData = getAccountData(conn, userID);
        commit(conn);

      } catch (Exception e) {
        Log.error("TradeDirect:getAccountData -- error getting account data", e);
        rollBack(conn, e);
      } finally {
        releaseConn(conn);
      }
      return accountData;
    } catch (Exception e) {
      throw new Exception(e.getMessage(), e);
    }
  }

  private AccountDataBean getAccountData(Connection conn, String userID) throws Exception {
    PreparedStatement stmt = getStatement(conn, getAccountForUserSQL);
    stmt.setString(1, userID);
    ResultSet rs = stmt.executeQuery();
    AccountDataBean accountData = getAccountDataFromResultSet(rs);
    stmt.close();
    return accountData;
  }

  /**
   * @see TradeServices#getAccountData(String)
   */
  public AccountDataBean getAccountData(int accountID) throws Exception {
    AccountDataBean accountData = null;
    Connection conn = null;
    try {
     
      Log.trace("TradeDirect:getAccountData - inSession(" + this.inSession + ")", new Integer(accountID));

      conn = getConn();
      accountData = getAccountData(accountID, conn);
      commit(conn);

    } catch (Exception e) {
      Log.error("TradeDirect:getAccountData -- error getting account data", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return accountData;
  }

  private AccountDataBean getAccountData(int accountID, Connection conn) throws Exception {
    PreparedStatement stmt = getStatement(conn, getAccountSQL);
    stmt.setInt(1, accountID);
    ResultSet rs = stmt.executeQuery();
    AccountDataBean accountData = getAccountDataFromResultSet(rs);
    stmt.close();
    return accountData;
  }

  private QuoteDataBean getQuoteData(Connection conn, String symbol) throws Exception {
    QuoteDataBean quoteData = null;
    PreparedStatement stmt = getStatement(conn, getQuoteSQL);
    stmt.setString(1, symbol);
    ResultSet rs = stmt.executeQuery();
    if (!rs.next()) {
      Log.error("TradeDirect:getQuoteData -- could not find quote for symbol=" + symbol);
    } else {
      quoteData = getQuoteDataFromResultSet(rs);
    }
    stmt.close();
    return quoteData;
  }

  private HoldingDataBean getHoldingData(int holdingID) throws Exception {
    HoldingDataBean holdingData = null;
    Connection conn = null;
    try {
      conn = getConn();
      holdingData = getHoldingData(conn, holdingID);
      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:getHoldingData -- error getting data", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return holdingData;
  }

  private HoldingDataBean getHoldingData(Connection conn, int holdingID) throws Exception {
    HoldingDataBean holdingData = null;
    PreparedStatement stmt = getStatement(conn, getHoldingSQL);
    stmt.setInt(1, holdingID);
    ResultSet rs = stmt.executeQuery();
    if (!rs.next()) {
      // already sold
      Log.debug("TradeDirect:getHoldingData -- no results -- holdingID=" + holdingID);
    } else {
      holdingData = getHoldingDataFromResultSet(rs);
    }

    stmt.close();
    return holdingData;
  }
  
  private OrderDataBean getOrderData(Connection conn, int orderID) throws Exception {
    OrderDataBean orderData = null;
    
    Log.trace("TradeDirect:getOrderData(conn, " + orderID + ")");
   
    PreparedStatement stmt = getStatement(conn, getOrderSQL);
    stmt.setInt(1, orderID);
    ResultSet rs = stmt.executeQuery();
    if (!rs.next()) {
      // already sold
      Log.error("TradeDirect:getOrderData -- no results for orderID:" + orderID);
    } else {
      orderData = getOrderDataFromResultSet(rs);
    }
    stmt.close();
    return orderData;
  }

  /**
   * @see TradeServices#getAccountProfileData(String)
   */
  @Override
  public AccountProfileDataBean getAccountProfileData(String userID) throws Exception {
    AccountProfileDataBean accountProfileData = null;
    Connection conn = null;

    try {
     
      Log.trace("TradeDirect:getAccountProfileData - inSession(" + this.inSession + ")", userID);
      

      conn = getConn();
      accountProfileData = getAccountProfileData(conn, userID);
      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:getAccountProfileData -- error getting profile data", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return accountProfileData;
  }

  private AccountProfileDataBean getAccountProfileData(Connection conn, String userID) throws Exception {
    PreparedStatement stmt = getStatement(conn, getAccountProfileSQL);
    stmt.setString(1, userID);

    ResultSet rs = stmt.executeQuery();

    AccountProfileDataBean accountProfileData = getAccountProfileDataFromResultSet(rs);
    stmt.close();
    return accountProfileData;
  }
  
  private AccountProfileDataBean getAccountProfileData(Connection conn, Integer accountID) throws Exception {
    PreparedStatement stmt = getStatement(conn, getAccountProfileForAccountSQL);
    stmt.setInt(1, accountID.intValue());

    ResultSet rs = stmt.executeQuery();

    AccountProfileDataBean accountProfileData = getAccountProfileDataFromResultSet(rs);
    stmt.close();
    return accountProfileData;
  }

  /**
   * @see TradeServices#updateAccountProfile(AccountProfileDataBean)
   */
  @Override
  public AccountProfileDataBean updateAccountProfile(AccountProfileDataBean profileData) throws Exception {
    AccountProfileDataBean accountProfileData = null;
    Connection conn = null;

    try {
      
      Log.trace("TradeDirect:updateAccountProfileData - inSession(" + this.inSession + ")", profileData.getUserID());

      conn = getConn();
      updateAccountProfile(conn, profileData);

      accountProfileData = getAccountProfileData(conn, profileData.getUserID());
      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:getAccountProfileData -- error getting profile data", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return accountProfileData;
  }

  private void creditAccountBalance(Connection conn, AccountDataBean accountData, BigDecimal credit) throws Exception {
    PreparedStatement stmt = getStatement(conn, creditAccountBalanceSQL);

    stmt.setBigDecimal(1, credit);
    stmt.setInt(2, accountData.getAccountID().intValue());

    stmt.executeUpdate();
    stmt.close();

  }

  // Set Timestamp to zero to denote sell is inflight
  // UPDATE -- could add a "status" attribute to holding
  private void updateHoldingStatus(Connection conn, Integer holdingID, String symbol) throws Exception {
    Timestamp ts = new Timestamp(0);
    PreparedStatement stmt = getStatement(conn, "update holdingejb set purchasedate= ? where holdingid = ?");

    stmt.setTimestamp(1, ts);
    stmt.setInt(2, holdingID.intValue());
    stmt.executeUpdate();
    stmt.close();
  }

  private void updateOrderStatus(Connection conn, Integer orderID, String status) throws Exception {
    PreparedStatement stmt = getStatement(conn, updateOrderStatusSQL);

    stmt.setString(1, status);
    stmt.setTimestamp(2, new Timestamp(System.currentTimeMillis()));
    stmt.setInt(3, orderID.intValue());
    stmt.executeUpdate();
    stmt.close();
  }

  private void updateOrderHolding(Connection conn, int orderID, int holdingID) throws Exception {
    PreparedStatement stmt = getStatement(conn, updateOrderHoldingSQL);

    stmt.setInt(1, holdingID);
    stmt.setInt(2, orderID);
    stmt.executeUpdate();
    stmt.close();
  }

  private void updateAccountProfile(Connection conn, AccountProfileDataBean profileData) throws Exception {
    PreparedStatement stmt = getStatement(conn, updateAccountProfileSQL);

    stmt.setString(1, profileData.getPassword());
    stmt.setString(2, profileData.getFullName());
    stmt.setString(3, profileData.getAddress());
    stmt.setString(4, profileData.getEmail());
    stmt.setString(5, profileData.getCreditCard());
    stmt.setString(6, profileData.getUserID());

    stmt.executeUpdate();
    stmt.close();
  }

  @Override
  public QuoteDataBean updateQuotePriceVolume(String symbol, BigDecimal changeFactor, double sharesTraded) throws Exception {
    return updateQuotePriceVolumeInt(symbol, changeFactor, sharesTraded, TradeConfig.getPublishQuotePriceChange());
  }

  /**
   * Update a quote's price and volume
   *
   * @param symbol
   *            The PK of the quote
   * @param changeFactor
   *            the percent to change the old price by (between 50% and 150%)
   * @param sharedTraded
   *            the ammount to add to the current volume
   * @param publishQuotePriceChange
   *            used by the PingJDBCWrite Primitive to ensure no JMS is used,
   *            should be true for all normal calls to this API
   */
  public QuoteDataBean updateQuotePriceVolumeInt(String symbol, BigDecimal changeFactor, double sharesTraded, boolean publishQuotePriceChange)
      throws Exception {

    if (TradeConfig.getUpdateQuotePrices() == false) {
      return new QuoteDataBean();
    }

    QuoteDataBean quoteData = null;
    Connection conn = null;

    try {
      Log.trace("TradeDirect:updateQuotePriceVolume - inSession(" + this.inSession + ")", symbol, changeFactor, new Double(sharesTraded));

      conn = getConn();

      quoteData = getQuoteForUpdate(conn, symbol);
      BigDecimal oldPrice = quoteData.getPrice();
      BigDecimal openPrice = quoteData.getOpen();

      double newVolume = quoteData.getVolume() + sharesTraded;

      if (oldPrice.equals(TradeConfig.PENNY_STOCK_PRICE)) {
        changeFactor = TradeConfig.PENNY_STOCK_RECOVERY_MIRACLE_MULTIPLIER;
      } else if (oldPrice.compareTo(TradeConfig.MAXIMUM_STOCK_PRICE) > 0) {
        changeFactor = TradeConfig.MAXIMUM_STOCK_SPLIT_MULTIPLIER;
      }

      BigDecimal newPrice = changeFactor.multiply(oldPrice).setScale(2, BigDecimal.ROUND_HALF_UP);
      double change = newPrice.subtract(openPrice).doubleValue();

      updateQuotePriceVolume(conn, quoteData.getSymbol(), newPrice, newVolume, change);
      quoteData = getQuote(conn, symbol);

      commit(conn);

      if (publishQuotePriceChange) {
        publishQuotePriceChange(quoteData, oldPrice, changeFactor, sharesTraded);
      }

      recentQuotePriceChangeList.add(quoteData);
      
    } catch (Exception e) {
      Log.error("TradeDirect:updateQuotePriceVolume -- error updating quote price/volume for symbol:" + symbol);
      rollBack(conn, e);
      throw e;
    } finally {
      releaseConn(conn);
    }
    return quoteData;
  }

  private void updateQuotePriceVolume(Connection conn, String symbol, BigDecimal newPrice, double newVolume, double change) throws Exception {

    PreparedStatement stmt = getStatement(conn, updateQuotePriceVolumeSQL);

    stmt.setBigDecimal(1, newPrice);
    stmt.setDouble(2, change);
    stmt.setDouble(3, newVolume);
    stmt.setString(4, symbol);

    stmt.executeUpdate();
    stmt.close();
  }

  private void publishQuotePriceChange(QuoteDataBean quoteData, BigDecimal oldPrice, BigDecimal changeFactor, double sharesTraded) throws Exception {

    Log.trace("TradeDirect:publishQuotePrice PUBLISHING to MDB quoteData = " + quoteData);       

    try (JMSContext context = topicConnectionFactory.createContext();){
      TextMessage message = context.createTextMessage();

      message.setStringProperty("command", "updateQuote");
      message.setStringProperty("symbol", quoteData.getSymbol());
      message.setStringProperty("company", quoteData.getCompanyName());
      message.setStringProperty("price", quoteData.getPrice().toString());
      message.setStringProperty("oldPrice", oldPrice.toString());
      message.setStringProperty("open", quoteData.getOpen().toString());
      message.setStringProperty("low", quoteData.getLow().toString());
      message.setStringProperty("high", quoteData.getHigh().toString());
      message.setDoubleProperty("volume", quoteData.getVolume());

      message.setStringProperty("changeFactor", changeFactor.toString());
      message.setDoubleProperty("sharesTraded", sharesTraded);
      message.setLongProperty("publishTime", System.currentTimeMillis());
      message.setText("Update Stock price for " + quoteData.getSymbol() + " old price = " + oldPrice + " new price = " + quoteData.getPrice());


      context.createProducer().send(tradeStreamerTopic, message);

    } catch (Exception e) {
      throw e; // pass exception back

    }
  }

  /**
   * @see TradeServices#login(String, String)
   */

  @Override
  public AccountDataBean login(String userID, String password) throws Exception {

    AccountDataBean accountData = null;
    Connection conn = null;
    try {
      Log.trace("TradeDirect:login - inSession(" + this.inSession + ")", userID, password);

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, getAccountProfileSQL);
      stmt.setString(1, userID);

      ResultSet rs = stmt.executeQuery();
      if (!rs.next()) {
        Log.error("TradeDirect:login -- failure to find account for" + userID);
        throw new javax.ejb.FinderException("Cannot find account for" + userID);
      }

      String pw = rs.getString("passwd");
      stmt.close();
      if ((pw == null) || (pw.equals(password) == false)) {
        String error = "TradeDirect:Login failure for user: " + userID + "\n\tIncorrect password-->" + userID + ":" + password;
        Log.error(error);
        throw new Exception(error);
      }

      stmt = getStatement(conn, loginSQL);
      stmt.setTimestamp(1, new Timestamp(System.currentTimeMillis()));
      stmt.setString(2, userID);

      stmt.executeUpdate();
      stmt.close();

      stmt = getStatement(conn, getAccountForUserSQL);
      stmt.setString(1, userID);
      rs = stmt.executeQuery();

      accountData = getAccountDataFromResultSet(rs);

      stmt.close();

      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:login -- error logging in user", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
    return accountData;

    /*
     * setLastLogin( new Timestamp(System.currentTimeMillis()) );
     * setLoginCount( getLoginCount() + 1 );
     */
  }

  /**
   * @see TradeServices#logout(String)
   */
  @Override
  public void logout(String userID) throws Exception {
    Log.trace("TradeDirect:logout - inSession(" + this.inSession + ")", userID);
    
    Connection conn = null;
    try {
      conn = getConn();
      PreparedStatement stmt = getStatement(conn, logoutSQL);
      stmt.setString(1, userID);
      stmt.executeUpdate();
      stmt.close();

      commit(conn);
    } catch (Exception e) {
      Log.error("TradeDirect:logout -- error logging out user", e);
      rollBack(conn, e);
    } finally {
      releaseConn(conn);
    }
  }

  /**
   * @see TradeServices#register(String, String, String, String, String,
   *      String, BigDecimal, boolean)
   */

  @Override
  public AccountDataBean register(String userID, String password, String fullname, String address, String email, String creditcard, BigDecimal openBalance)
      throws Exception {

    AccountDataBean accountData = null;
    Connection conn = null;
    try {
      Log.trace("TradeDirect:register - inSession(" + this.inSession + ")");  

      conn = getConn();
      PreparedStatement stmt = getStatement(conn, createAccountSQL);

      Integer accountID = KeySequenceDirect.getNextID(conn, "account", inSession, getInGlobalTxn());
      BigDecimal balance = openBalance;
      Timestamp creationDate = new Timestamp(System.currentTimeMillis());
      Timestamp lastLogin = creationDate;
      int loginCount = 0;
      int logoutCount = 0;

      stmt.setInt(1, accountID.intValue());
      stmt.setTimestamp(2, creationDate);
      stmt.setBigDecimal(3, openBalance);
      stmt.setBigDecimal(4, balance);
      stmt.setTimestamp(5, lastLogin);
      stmt.setInt(6, loginCount);
      stmt.setInt(7, logoutCount);
      stmt.setString(8, userID);
      stmt.executeUpdate();
      stmt.close();

      stmt = getStatement(conn, createAccountProfileSQL);
      stmt.setString(1, userID);
      stmt.setString(2, password);
      stmt.setString(3, fullname);
      stmt.setString(4, address);
      stmt.setString(5, email);
      stmt.setString(6, creditcard);
      stmt.executeUpdate();
      stmt.close();

      commit(conn);

      accountData = new AccountDataBean(accountID, loginCount, logoutCount, lastLogin, creationDate, balance, openBalance, userID);

    } catch (Exception e) {
      Log.error("TradeDirect:register -- error registering new user", e);
    } finally {
      releaseConn(conn);
    }
    return accountData;
  }

  private AccountDataBean getAccountDataFromResultSet(ResultSet rs) throws Exception {
    AccountDataBean accountData = null;

    if (!rs.next()) {
      Log.error("TradeDirect:getAccountDataFromResultSet -- cannot find account data");
    } else {
      accountData = new AccountDataBean(new Integer(rs.getInt("accountID")), rs.getInt("loginCount"), rs.getInt("logoutCount"),
          rs.getTimestamp("lastLogin"), rs.getTimestamp("creationDate"), rs.getBigDecimal("balance"), rs.getBigDecimal("openBalance"),
          rs.getString("profile_userID"));
    }
    return accountData;
  }

  private AccountProfileDataBean getAccountProfileDataFromResultSet(ResultSet rs) throws Exception {
    AccountProfileDataBean accountProfileData = null;

    if (!rs.next()) {
      Log.error("TradeDirect:getAccountProfileDataFromResultSet -- cannot find accountprofile data");
    } else {
      accountProfileData = new AccountProfileDataBean(rs.getString("userID"), rs.getString("passwd"), rs.getString("fullName"), rs.getString("address"),
          rs.getString("email"), rs.getString("creditCard"));
    }

    return accountProfileData;
  }

  private HoldingDataBean getHoldingDataFromResultSet(ResultSet rs) throws Exception {
    HoldingDataBean holdingData = null;

    holdingData = new HoldingDataBean(new Integer(rs.getInt("holdingID")), rs.getDouble("quantity"), rs.getBigDecimal("purchasePrice"),
        rs.getTimestamp("purchaseDate"), rs.getString("quote_symbol"));
    return holdingData;
  }

  private QuoteDataBean getQuoteDataFromResultSet(ResultSet rs) throws Exception {
    QuoteDataBean quoteData = null;

    quoteData = new QuoteDataBean(rs.getString("symbol"), rs.getString("companyName"), rs.getDouble("volume"), rs.getBigDecimal("price"),
        rs.getBigDecimal("open1"), rs.getBigDecimal("low"), rs.getBigDecimal("high"), rs.getDouble("change1"));
    return quoteData;
  }

  private OrderDataBean getOrderDataFromResultSet(ResultSet rs) throws Exception {
    OrderDataBean orderData = null;

    orderData = new OrderDataBean(new Integer(rs.getInt("orderID")), rs.getString("orderType"), rs.getString("orderStatus"), rs.getTimestamp("openDate"),
        rs.getTimestamp("completionDate"), rs.getDouble("quantity"), rs.getBigDecimal("price"), rs.getBigDecimal("orderFee"),
        rs.getString("quote_symbol"));
    return orderData;
  }

  public boolean recreateDBTables(Object[] sqlBuffer, java.io.PrintWriter out) throws Exception {
    // Clear MDB Statistics
    MDBStats.getInstance().reset();

    Connection conn = null;
    boolean success = false;
    try {
     
      Log.trace("TradeDirect:recreateDBTables");

      conn = getConn();
      Statement stmt = conn.createStatement();
      int bufferLength = sqlBuffer.length;
      for (int i = 0; i < bufferLength; i++) {
        try {
          stmt.executeUpdate((String) sqlBuffer[i]);
          // commit(conn);
        } catch (SQLException ex) {
          // Ignore DROP statements as tables won't always exist.
          if (((String) sqlBuffer[i]).indexOf("DROP ") < 0) {
            Log.error("TradeDirect:recreateDBTables SQL Exception thrown on executing the foll sql command: " + sqlBuffer[i], ex);
            out.println("<BR>SQL Exception thrown on executing the foll sql command: <I>" + sqlBuffer[i] + "</I> . Check log for details.</BR>");
          }
        }
      }
      stmt.close();
      commit(conn);
      success = true;
    } catch (Exception e) {
      Log.error(e, "TradeDirect:recreateDBTables() -- Error dropping and recreating the database tables");
    } finally {
      releaseConn(conn);
    }
    return success;
  }

  ;



  private void releaseConn(Connection conn) throws Exception {
    try {
      if (conn != null) {
        conn.close();
        if (Log.doTrace()) {
          synchronized (lock) {
            connCount--;
          }
          Log.trace("TradeDirect:releaseConn -- connection closed, connCount=" + connCount);
        }
      }
    } catch (Exception e) {
      Log.error("TradeDirect:releaseConnection -- failed to close connection", e);
    }
  }


  /*
   * Allocate a new connection to the datasource
   */
  private static int connCount = 0;

  private static Integer lock = new Integer(0);

  private Connection getConn() throws Exception {

    Connection conn = datasource.getConnection();      

    if (!this.inGlobalTxn) {
      conn.setAutoCommit(false);
    }
    if (Log.doTrace()) {
      synchronized (lock) {
        connCount++;
      }
      Log.trace("TradeDirect:getConn -- new connection allocated, IsolationLevel=" + conn.getTransactionIsolation() + " connectionCount = " + connCount);
    }

    return conn;
  }

  public Connection getConnPublic() throws Exception {
    return getConn();
  }

  /*
   * Commit the provided connection if not under Global Transaction scope -
   * conn.commit() is not allowed in a global transaction. the txn manager
   * will perform the commit
   */
  private void commit(Connection conn) throws Exception {
    if (!inSession) {
      if ((getInGlobalTxn() == false) && (conn != null)) {
        conn.commit();
      }
    }
  }

  /*
   * Rollback the statement for the given connection
   */
  private void rollBack(Connection conn, Exception e) throws Exception {
    if (!inSession) {
      Log.log("TradeDirect:rollBack -- rolling back conn due to previously caught exception -- inGlobalTxn=" + getInGlobalTxn());
      if ((getInGlobalTxn() == false) && (conn != null)) {
        conn.rollback();
      } else {
        throw e; // Throw the exception
        // so the Global txn manager will rollBack
      }
    }
  }

  /*
   * Allocate a new prepared statment for this connection
   */
  private PreparedStatement getStatement(Connection conn, String sql) throws Exception {
    return conn.prepareStatement(sql);
  }

  private PreparedStatement getStatement(Connection conn, String sql, int type, int concurrency) throws Exception {
    return conn.prepareStatement(sql, type, concurrency);
  }

  private static final String createQuoteSQL = "insert into quoteejb " + "( symbol, companyName, volume, price, open1, low, high, change1 ) "
      + "VALUES (  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  )";

  private static final String createAccountSQL = "insert into accountejb "
      + "( accountid, creationDate, openBalance, balance, lastLogin, loginCount, logoutCount, profile_userid) "
      + "VALUES (  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  )";

  private static final String createAccountProfileSQL = "insert into accountprofileejb " + "( userid, passwd, fullname, address, email, creditcard ) "
      + "VALUES (  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  )";

  private static final String createHoldingSQL = "insert into holdingejb "
      + "( holdingid, purchaseDate, purchasePrice, quantity, quote_symbol, account_accountid ) " + "VALUES (  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ? )";

  private static final String createOrderSQL = "insert into orderejb "
      + "( orderid, ordertype, orderstatus, opendate, quantity, price, orderfee, account_accountid,  holding_holdingid, quote_symbol) "
      + "VALUES (  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  ,  ?  , ? , ? , ?)";

  private static final String removeHoldingSQL = "delete from holdingejb where holdingid = ?";

  private static final String removeHoldingFromOrderSQL = "update orderejb set holding_holdingid=null where holding_holdingid = ?";

  private static final String updateAccountProfileSQL = "update accountprofileejb set " + "passwd = ?, fullname = ?, address = ?, email = ?, creditcard = ? "
      + "where userid = (select profile_userid from accountejb a " + "where a.profile_userid=?)";

  private static final String loginSQL = "update accountejb set lastLogin=?, logincount=logincount+1 " + "where profile_userid=?";

  private static final String logoutSQL = "update accountejb set logoutcount=logoutcount+1 " + "where profile_userid=?";

  private static final String getAccountSQL = "select * from accountejb a where a.accountid = ?";

  private static final String getAccountProfileSQL = "select * from accountprofileejb ap where ap.userid = "
      + "(select profile_userid from accountejb a where a.profile_userid=?)";

  private static final String getAccountProfileForAccountSQL = "select * from accountprofileejb ap where ap.userid = "
      + "(select profile_userid from accountejb a where a.accountid=?)";

  private static final String getAccountForUserSQL = "select * from accountejb a where a.profile_userid = "
      + "( select userid from accountprofileejb ap where ap.userid = ?)";

  private static final String getHoldingSQL = "select * from holdingejb h where h.holdingid = ?";

  private static final String getHoldingsForUserSQL = "select * from holdingejb h where h.account_accountid = "
      + "(select a.accountid from accountejb a where a.profile_userid = ?)";

  private static final String getOrderSQL = "select * from orderejb o where o.orderid = ?";

  private static final String getOrdersByUserSQL = "select * from orderejb o where o.account_accountid = "
      + "(select a.accountid from accountejb a where a.profile_userid = ?)";

  private static final String getClosedOrdersSQL = "select * from orderejb o " + "where o.orderstatus = 'closed' AND o.account_accountid = "
      + "(select a.accountid from accountejb a where a.profile_userid = ?)";

  private static final String getQuoteSQL = "select * from quoteejb q where q.symbol=?";

  private static final String getAllQuotesSQL = "select * from quoteejb q";

  private static final String getQuoteForUpdateSQL = "select * from quoteejb q where q.symbol=? For Update";

  private static final String getTSIAQuotesOrderByChangeSQL = "select * from quoteejb q order by q.change1";

  private static final String getTSIASQL = "select SUM(price)/count(*) as TSIA from quoteejb q ";

  private static final String getOpenTSIASQL = "select SUM(open1)/count(*) as openTSIA from quoteejb q ";

  private static final String getTSIATotalVolumeSQL = "select SUM(volume) as totalVolume from quoteejb q ";

  private static final String creditAccountBalanceSQL = "update accountejb set " + "balance = balance + ? " + "where accountid = ?";

  private static final String updateOrderStatusSQL = "update orderejb set " + "orderstatus = ?, completiondate = ? " + "where orderid = ?";

  private static final String updateOrderHoldingSQL = "update orderejb set " + "holding_holdingID = ? " + "where orderid = ?";

  private static final String updateQuotePriceVolumeSQL = "update quoteejb set " + "price = ?, change1 = ?, volume = ? " + "where symbol = ?";

  /**
   * Gets the inGlobalTxn
   *
   * @return Returns a boolean
   */
  private boolean getInGlobalTxn() {
    return inGlobalTxn;
  }

  /**
   * Sets the inGlobalTxn
   *
   * @param inGlobalTxn
   *            The inGlobalTxn to set
   */
  private void setInGlobalTxn(boolean inGlobalTxn) {
    this.inGlobalTxn = inGlobalTxn;
  }
  
  public void setInSession(boolean inSession) {
    this.inSession = inSession;
  }

  @Override
  public int getImpl() {
    return TradeConfig.DIRECT;
  }



  @Override
  public QuoteDataBean pingTwoPhase(String symbol) {
    throw new UnsupportedOperationException();
  }



  @Override
  public double investmentReturn(double rnd1, double rnd2) {
    throw new UnsupportedOperationException();
  }   
}
