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
package com.ibm.websphere.samples.daytrader.beans;

import java.io.Serializable;

public class RunStatsDataBean implements Serializable {
    private static final long serialVersionUID = 4017778674103242167L;

    // Constructors
    public RunStatsDataBean() {
    }

    // count of trade users in the database (users w/ userID like 'uid:%')
    private int tradeUserCount;
    // count of trade stocks in the database (stocks w/ symbol like 's:%')
    private int tradeStockCount;

    // count of new registered users in this run (users w/ userID like 'ru:%')
    // -- random user
    private int newUserCount;

    // sum of logins by trade users
    private int sumLoginCount;
    // sum of logouts by trade users
    private int sumLogoutCount;

    // count of holdings of trade users
    private int holdingCount;

    // count of orders of trade users
    private int orderCount;
    // count of buy orders of trade users
    private int buyOrderCount;
    // count of sell orders of trade users
    private int sellOrderCount;
    // count of cancelled orders of trade users
    private int cancelledOrderCount;
    // count of open orders of trade users
    private int openOrderCount;
    // count of orders deleted during this trade Reset
    private int deletedOrderCount;

    @Override
    public String toString() {
        return "\n\tRunStatsData for reset at " + new java.util.Date() + "\n\t\t      tradeUserCount: " + getTradeUserCount() + "\n\t\t        newUserCount: "
                + getNewUserCount() + "\n\t\t       sumLoginCount: " + getSumLoginCount() + "\n\t\t      sumLogoutCount: " + getSumLogoutCount()
                + "\n\t\t        holdingCount: " + getHoldingCount() + "\n\t\t          orderCount: " + getOrderCount() + "\n\t\t       buyOrderCount: "
                + getBuyOrderCount() + "\n\t\t      sellOrderCount: " + getSellOrderCount() + "\n\t\t cancelledOrderCount: " + getCancelledOrderCount()
                + "\n\t\t      openOrderCount: " + getOpenOrderCount() + "\n\t\t   deletedOrderCount: " + getDeletedOrderCount();
    }

    /**
     * Gets the tradeUserCount
     *
     * @return Returns a int
     */
    public int getTradeUserCount() {
        return tradeUserCount;
    }

    /**
     * Sets the tradeUserCount
     *
     * @param tradeUserCount
     *            The tradeUserCount to set
     */
    public void setTradeUserCount(int tradeUserCount) {
        this.tradeUserCount = tradeUserCount;
    }

    /**
     * Gets the newUserCount
     *
     * @return Returns a int
     */
    public int getNewUserCount() {
        return newUserCount;
    }

    /**
     * Sets the newUserCount
     *
     * @param newUserCount
     *            The newUserCount to set
     */
    public void setNewUserCount(int newUserCount) {
        this.newUserCount = newUserCount;
    }

    /**
     * Gets the sumLoginCount
     *
     * @return Returns a int
     */
    public int getSumLoginCount() {
        return sumLoginCount;
    }

    /**
     * Sets the sumLoginCount
     *
     * @param sumLoginCount
     *            The sumLoginCount to set
     */
    public void setSumLoginCount(int sumLoginCount) {
        this.sumLoginCount = sumLoginCount;
    }

    /**
     * Gets the sumLogoutCount
     *
     * @return Returns a int
     */
    public int getSumLogoutCount() {
        return sumLogoutCount;
    }

    /**
     * Sets the sumLogoutCount
     *
     * @param sumLogoutCount
     *            The sumLogoutCount to set
     */
    public void setSumLogoutCount(int sumLogoutCount) {
        this.sumLogoutCount = sumLogoutCount;
    }

    /**
     * Gets the holdingCount
     *
     * @return Returns a int
     */
    public int getHoldingCount() {
        return holdingCount;
    }

    /**
     * Sets the holdingCount
     *
     * @param holdingCount
     *            The holdingCount to set
     */
    public void setHoldingCount(int holdingCount) {
        this.holdingCount = holdingCount;
    }

    /**
     * Gets the buyOrderCount
     *
     * @return Returns a int
     */
    public int getBuyOrderCount() {
        return buyOrderCount;
    }

    /**
     * Sets the buyOrderCount
     *
     * @param buyOrderCount
     *            The buyOrderCount to set
     */
    public void setBuyOrderCount(int buyOrderCount) {
        this.buyOrderCount = buyOrderCount;
    }

    /**
     * Gets the sellOrderCount
     *
     * @return Returns a int
     */
    public int getSellOrderCount() {
        return sellOrderCount;
    }

    /**
     * Sets the sellOrderCount
     *
     * @param sellOrderCount
     *            The sellOrderCount to set
     */
    public void setSellOrderCount(int sellOrderCount) {
        this.sellOrderCount = sellOrderCount;
    }

    /**
     * Gets the cancelledOrderCount
     *
     * @return Returns a int
     */
    public int getCancelledOrderCount() {
        return cancelledOrderCount;
    }

    /**
     * Sets the cancelledOrderCount
     *
     * @param cancelledOrderCount
     *            The cancelledOrderCount to set
     */
    public void setCancelledOrderCount(int cancelledOrderCount) {
        this.cancelledOrderCount = cancelledOrderCount;
    }

    /**
     * Gets the openOrderCount
     *
     * @return Returns a int
     */
    public int getOpenOrderCount() {
        return openOrderCount;
    }

    /**
     * Sets the openOrderCount
     *
     * @param openOrderCount
     *            The openOrderCount to set
     */
    public void setOpenOrderCount(int openOrderCount) {
        this.openOrderCount = openOrderCount;
    }

    /**
     * Gets the deletedOrderCount
     *
     * @return Returns a int
     */
    public int getDeletedOrderCount() {
        return deletedOrderCount;
    }

    /**
     * Sets the deletedOrderCount
     *
     * @param deletedOrderCount
     *            The deletedOrderCount to set
     */
    public void setDeletedOrderCount(int deletedOrderCount) {
        this.deletedOrderCount = deletedOrderCount;
    }

    /**
     * Gets the orderCount
     *
     * @return Returns a int
     */
    public int getOrderCount() {
        return orderCount;
    }

    /**
     * Sets the orderCount
     *
     * @param orderCount
     *            The orderCount to set
     */
    public void setOrderCount(int orderCount) {
        this.orderCount = orderCount;
    }

    /**
     * Gets the tradeStockCount
     *
     * @return Returns a int
     */
    public int getTradeStockCount() {
        return tradeStockCount;
    }

    /**
     * Sets the tradeStockCount
     *
     * @param tradeStockCount
     *            The tradeStockCount to set
     */
    public void setTradeStockCount(int tradeStockCount) {
        this.tradeStockCount = tradeStockCount;
    }

}
