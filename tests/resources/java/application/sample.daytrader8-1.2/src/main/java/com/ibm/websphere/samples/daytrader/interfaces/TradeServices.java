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
package com.ibm.websphere.samples.daytrader.interfaces;

import java.math.BigDecimal;
import java.util.Collection;
import java.util.concurrent.Future;

import com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountProfileDataBean;
import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;
import com.ibm.websphere.samples.daytrader.entities.OrderDataBean;
import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;

/**
 * TradeServices interface specifies the business methods provided by the Trade
 * online broker application. These business methods represent the features and
 * operations that can be performed by customers of the brokerage such as login,
 * logout, get a stock quote, buy or sell a stock, etc. This interface is
 * implemented by {@link Trade} providing an EJB implementation of these
 * business methods and also by {@link TradeDirect} providing a JDBC
 * implementation.
 *
 * @see TradeDirect
 * @see TradeSLSB
 *
 */

public interface TradeServices {

    /**
     * Compute and return a snapshot of the current market conditions This
     * includes the TSIA - an index of the price of the top 100 Trade stock
     * quotes The openTSIA ( the index at the open) The volume of shares traded,
     * Top Stocks gain and loss
     *
     * @return A snapshot of the current market summary
     */
    MarketSummaryDataBean getMarketSummary() throws Exception;

    /**
     * Create an order (buy or sell)
     *
     * @param accoount
     *            the accountdatabean
     * @param quote
     *            the quptedatabean
     * @param holding
     *            the holdingdatabean
     * @param orderType
     *            buy or sell
     * @param quantity
     *            quantity            
     * @return Collection OrderDataBeans providing detailed order information
     */
    OrderDataBean createOrder(AccountDataBean account, QuoteDataBean quote, HoldingDataBean holding, String orderType,
        double quantity) throws Exception;
    
    /**
     * Purchase a stock and create a new holding for the given user. Given a
     * stock symbol and quantity to purchase, retrieve the current quote price,
     * debit the user's account balance, and add holdings to user's portfolio.
     * buy/sell are asynchronous, using J2EE messaging, A new order is created
     * and submitted for processing to the TradeBroker
     *
     * @param userID
     *            the customer requesting the stock purchase
     * @param symbol
     *            the symbol of the stock being purchased
     * @param quantity
     *            the quantity of shares to purchase
     * @return OrderDataBean providing the status of the newly created buy order
     */

    OrderDataBean buy(String userID, String symbol, double quantity, int orderProcessingMode) throws Exception;

    /**
     * Sell a stock holding and removed the holding for the given user. Given a
     * Holding, retrieve current quote, credit user's account, and reduce
     * holdings in user's portfolio.
     *
     * @param userID
     *            the customer requesting the sell
     * @param holdingID
     *            the users holding to be sold
     * @return OrderDataBean providing the status of the newly created sell
     *         order
     */
    OrderDataBean sell(String userID, Integer holdingID, int orderProcessingMode) throws Exception;

    /**
     * Queue the Order identified by orderID to be processed
     *
     * Orders are submitted through JMS to a Trading Broker and completed
     * asynchronously. This method queues the order for processing
     *
     * The boolean twoPhase specifies to the server implementation whether or
     * not the method is to participate in a global transaction
     *
     * @param orderID
     *            the Order being queued for processing
     * @return OrderDataBean providing the status of the completed order
     */
    void queueOrder(Integer orderID, boolean twoPhase) throws Exception;

    /**
     * Complete the Order identified by orderID. This method completes
     * the order For a buy, the stock is purchased creating a holding and the
     * users account is debited For a sell, the stock holding is removed and the
     * users account is credited with the proceeds
     *
     * The boolean twoPhase specifies to the server implementation whether or
     * not the method is to participate in a global transaction
     *
     * @param orderID
     *            the Order to complete
     * @return OrderDataBean providing the status of the completed order
     */
    OrderDataBean completeOrder(Integer orderID, boolean twoPhase) throws Exception;

    /**
     * Complete the Order identefied by orderID Orders are completed 
     * asynchronously. This method completes
     * the order For a buy, the stock is purchased creating a holding and the
     * users account is debited For a sell, the stock holding is removed and the
     * users account is credited with the proceeds
     *
     * The boolean twoPhase specifies to the server implementation whether or
     * not the method is to participate in a global transaction
     *
     * @param orderID
     *            the Order to complete
     * @return OrderDataBean providing the status of the completed order
     */
    Future<OrderDataBean> completeOrderAsync(Integer orderID, boolean twoPhase) throws Exception;
    
    /**
     * Cancel the Order identefied by orderID
     *
     * The boolean twoPhase specifies to the server implementation whether or
     * not the method is to participate in a global transaction
     *
     * @param orderID
     *            the Order to complete
     * @return OrderDataBean providing the status of the completed order
     */
    void cancelOrder(Integer orderID, boolean twoPhase) throws Exception;

    /**
     * Signify an order has been completed for the given userID
     *
     * @param userID
     *            the user for which an order has completed
     * @param orderID
     *            the order which has completed
     *
     */
    void orderCompleted(String userID, Integer orderID) throws Exception;

    /**
     * Get the collection of all orders for a given account
     *
     * @param userID
     *            the customer account to retrieve orders for
     * @return Collection OrderDataBeans providing detailed order information
     */
    Collection<?> getOrders(String userID) throws Exception;

    /**
     * Get the collection of completed orders for a given account that need to
     * be alerted to the user
     *
     * @param userID
     *            the customer account to retrieve orders for
     * @return Collection OrderDataBeans providing detailed order information
     */
    Collection<?> getClosedOrders(String userID) throws Exception;

    /**
     * Given a market symbol, price, and details, create and return a new
     * {@link QuoteDataBean}
     *
     * @param symbol
     *            the symbol of the stock
     * @param price
     *            the current stock price
     * @param details
     *            a short description of the stock or company
     * @return a new QuoteDataBean or null if Quote could not be created
     */
    QuoteDataBean createQuote(String symbol, String companyName, BigDecimal price) throws Exception;

    /**
     * Return a {@link QuoteDataBean} describing a current quote for the given
     * stock symbol
     *
     * @param symbol
     *            the stock symbol to retrieve the current Quote
     * @return the QuoteDataBean
     */
    QuoteDataBean getQuote(String symbol) throws Exception;

    /**
     * Return a {@link java.util.Collection} of {@link QuoteDataBean} describing
     * all current quotes
     *
     * @return A collection of QuoteDataBean
     */
    Collection<?> getAllQuotes() throws Exception;

    /**
     * Update the stock quote price and volume for the specified stock symbol
     *
     * @param symbol
     *            for stock quote to update
     * @param price
     *            the updated quote price
     * @return the QuoteDataBean describing the stock
     */
    QuoteDataBean updateQuotePriceVolume(String symbol, BigDecimal newPrice, double sharesTraded) throws Exception;

    /**
     * Return the portfolio of stock holdings for the specified customer as a
     * collection of HoldingDataBeans
     *
     * @param userID
     *            the customer requesting the portfolio
     * @return Collection of the users portfolio of stock holdings
     */
    Collection<HoldingDataBean> getHoldings(String userID) throws Exception;

    /**
     * Return a specific user stock holding identifed by the holdingID
     *
     * @param holdingID
     *            the holdingID to return
     * @return a HoldingDataBean describing the holding
     */
    HoldingDataBean getHolding(Integer holdingID) throws Exception;

    /**
     * Return an AccountDataBean object for userID describing the account
     *
     * @param userID
     *            the account userID to lookup
     * @return User account data in AccountDataBean
     */
    AccountDataBean getAccountData(String userID) throws Exception;

    /**
     * Return an AccountProfileDataBean for userID providing the users profile
     *
     * @param userID
     *            the account userID to lookup
     * @param User
     *            account profile data in AccountProfileDataBean
     */
    AccountProfileDataBean getAccountProfileData(String userID) throws Exception;

    /**
     * Update userID's account profile information using the provided
     * AccountProfileDataBean object
     *
     * @param userID
     *            the account userID to lookup
     * @param User
     *            account profile data in AccountProfileDataBean
     */
    AccountProfileDataBean updateAccountProfile(AccountProfileDataBean profileData) throws Exception;

    /**
     * Attempt to authenticate and login a user with the given password
     *
     * @param userID
     *            the customer to login
     * @param password
     *            the password entered by the customer for authentication
     * @return User account data in AccountDataBean
     */
    AccountDataBean login(String userID, String password) throws Exception;

    /**
     * Logout the given user
     *
     * @param userID
     *            the customer to logout
     * @return the login status
     */

    void logout(String userID) throws Exception;

    /**
     * Register a new Trade customer. Create a new user profile, user registry
     * entry, account with initial balance, and empty portfolio.
     *
     * @param userID
     *            the new customer to register
     * @param password
     *            the customers password
     * @param fullname
     *            the customers fullname
     * @param address
     *            the customers street address
     * @param email
     *            the customers email address
     * @param creditcard
     *            the customers creditcard number
     * @param initialBalance
     *            the amount to charge to the customers credit to open the
     *            account and set the initial balance
     * @return the userID if successful, null otherwise
     */
    AccountDataBean register(String userID, String password, String fullname, String address, String email, String creditcard, BigDecimal openBalance)
            throws Exception;

    
    int getImpl();

    QuoteDataBean pingTwoPhase(String symbol) throws Exception;

    double investmentReturn(double rnd1, double rnd2) throws Exception;

    void setInSession(boolean inSession);
}
