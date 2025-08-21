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
package com.ibm.websphere.samples.daytrader.entities;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.FetchType;
import javax.persistence.GeneratedValue;
import javax.persistence.GenerationType;
import javax.persistence.Id;
import javax.persistence.JoinColumn;
import javax.persistence.ManyToOne;
import javax.persistence.Table;
import javax.persistence.TableGenerator;
import javax.persistence.Temporal;
import javax.persistence.TemporalType;
import javax.persistence.Transient;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.PastOrPresent;
import javax.validation.constraints.Positive;

import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

@Entity(name = "holdingejb")
@Table(name = "holdingejb")
public class HoldingDataBean implements Serializable {

    /* persistent/relationship fields */

    private static final long serialVersionUID = -2338411656251935480L;

    @Id
    @TableGenerator(name = "holdingIdGen", table = "KEYGENEJB", pkColumnName = "KEYNAME", valueColumnName = "KEYVAL", pkColumnValue = "holding", allocationSize = 1000)
    @GeneratedValue(strategy = GenerationType.TABLE, generator = "holdingIdGen")
    @Column(name = "HOLDINGID", nullable = false)
    private Integer holdingID; /* holdingID */

    @NotNull
    @Positive
    @Column(name = "QUANTITY", nullable = false)
    private double quantity; /* quantity */

    @Column(name = "PURCHASEPRICE")
    @Positive
    private BigDecimal purchasePrice; /* purchasePrice */

    @Column(name = "PURCHASEDATE")
    @Temporal(TemporalType.TIMESTAMP)
    @PastOrPresent
    private Date purchaseDate; /* purchaseDate */

    @Transient
    private String quoteID; /* Holding(*) ---> Quote(1) */

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "ACCOUNT_ACCOUNTID")
    private AccountDataBean account;

    @ManyToOne(fetch = FetchType.EAGER)
    @JoinColumn(name = "QUOTE_SYMBOL")
    private QuoteDataBean quote;

    public HoldingDataBean() {
    }

    public HoldingDataBean(Integer holdingID, double quantity, BigDecimal purchasePrice, Date purchaseDate, String quoteID) {
        setHoldingID(holdingID);
        setQuantity(quantity);
        setPurchasePrice(purchasePrice);
        setPurchaseDate(purchaseDate);
        setQuoteID(quoteID);
    }

    public HoldingDataBean(double quantity, BigDecimal purchasePrice, Date purchaseDate, AccountDataBean account, QuoteDataBean quote) {
        setQuantity(quantity);
        setPurchasePrice(purchasePrice);
        setPurchaseDate(purchaseDate);
        setAccount(account);
        setQuote(quote);
    }

    public static HoldingDataBean getRandomInstance() {
        return new HoldingDataBean(new Integer(TradeConfig.rndInt(100000)), // holdingID
                TradeConfig.rndQuantity(), // quantity
                TradeConfig.rndBigDecimal(1000.0f), // purchasePrice
                new java.util.Date(TradeConfig.rndInt(Integer.MAX_VALUE)), // purchaseDate
                TradeConfig.rndSymbol() // symbol
        );
    }

    @Override
    public String toString() {
        return "\n\tHolding Data for holding: " + getHoldingID() + "\n\t\t      quantity:" + getQuantity() + "\n\t\t purchasePrice:" + getPurchasePrice()
                + "\n\t\t  purchaseDate:" + getPurchaseDate() + "\n\t\t       quoteID:" + getQuoteID();
    }

    public String toHTML() {
        return "<BR>Holding Data for holding: " + getHoldingID() + "</B>" + "<LI>      quantity:" + getQuantity() + "</LI>" + "<LI> purchasePrice:"
                + getPurchasePrice() + "</LI>" + "<LI>  purchaseDate:" + getPurchaseDate() + "</LI>" + "<LI>       quoteID:" + getQuoteID() + "</LI>";
    }

    public void print() {
        Log.log(this.toString());
    }

    public Integer getHoldingID() {
        return holdingID;
    }

    public void setHoldingID(Integer holdingID) {
        this.holdingID = holdingID;
    }

    public double getQuantity() {
        return quantity;
    }

    public void setQuantity(double quantity) {
        this.quantity = quantity;
    }

    public BigDecimal getPurchasePrice() {
        return purchasePrice;
    }

    public void setPurchasePrice(BigDecimal purchasePrice) {
        this.purchasePrice = purchasePrice;
    }

    public Date getPurchaseDate() {
        return purchaseDate;
    }

    public void setPurchaseDate(Date purchaseDate) {
        this.purchaseDate = purchaseDate;
    }

    public String getQuoteID() {
        if (quote != null) {
            return quote.getSymbol();
        }
        return quoteID;
    }

    public void setQuoteID(String quoteID) {
        this.quoteID = quoteID;
    }

    public AccountDataBean getAccount() {
        return account;
    }

    public void setAccount(AccountDataBean account) {
        this.account = account;
    }

    public QuoteDataBean getQuote() {
        return quote;
    }

    public void setQuote(QuoteDataBean quote) {
        this.quote = quote;
    }

    @Override
    public int hashCode() {
        int hash = 0;
        hash += (this.holdingID != null ? this.holdingID.hashCode() : 0);
        return hash;
    }

    @Override
    public boolean equals(Object object) {
        
        if (!(object instanceof HoldingDataBean)) {
            return false;
        }
        HoldingDataBean other = (HoldingDataBean) object;

        if (this.holdingID != other.holdingID && (this.holdingID == null || !this.holdingID.equals(other.holdingID))) {
            return false;
        }

        return true;
    }
}
