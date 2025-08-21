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
import java.text.DecimalFormat;

import com.ibm.websphere.samples.daytrader.util.FinancialUtils;

public class QuoteData {
    private BigDecimal price;
    private BigDecimal open;
    private String symbol;
    private BigDecimal high;
    private BigDecimal low;
    private String companyName;
    private double volume;
    private double change;
    private String range;
    private BigDecimal gainPercent;
    private BigDecimal gain;

    public QuoteData(BigDecimal price, BigDecimal open, String symbol) {
        this.open = open;
        this.price = price;
        this.symbol = symbol;
        this.change = price.subtract(open).setScale(2).doubleValue();
    }

    public QuoteData(BigDecimal open, BigDecimal price, String symbol, BigDecimal high, BigDecimal low, String companyName, Double volume, Double change) {
        this.open = open;
        this.price = price;
        this.symbol = symbol;
        this.high = high;
        this.low = low;
        this.companyName = companyName;
        this.volume = volume;
        this.change = change;
        this.range = high.toString() + "-" + low.toString();
        this.gainPercent = FinancialUtils.computeGainPercent(price, open).setScale(2);
        this.gain = FinancialUtils.computeGain(price, open).setScale(2);
    }

    public void setSymbol(String symbol) {
        this.symbol = symbol;
    }

    public String getSymbol() {
        return symbol;
    }

    public void setPrice(BigDecimal price) {
        this.price = price;
    }

    public BigDecimal getPrice() {
        return price;
    }

    public void setOpen(BigDecimal open) {
        this.open = open;
    }

    public BigDecimal getOpen() {
        return open;
    }

    public void setHigh(BigDecimal high) {
        this.high = high;
    }

    public BigDecimal getHigh() {
        return high;
    }

    public void setLow(BigDecimal low) {
        this.low = low;
    }

    public BigDecimal getLow() {
        return low;
    }

    public void setCompanyName(String companyName) {
        this.companyName = companyName;
    }

    public String getCompanyName() {
        return companyName;
    }

    public void setVolume(double volume) {
        this.volume = volume;
    }

    public double getVolume() {
        return volume;
    }

    public void setChange(double change) {
        this.change = change;
    }

    public double getChange() {
        return change;
    }

    public void setRange(String range) {
        this.range = range;
    }

    public String getRange() {
        return range;
    }

    public void setGainPercent(BigDecimal gainPercent) {
        this.gainPercent = gainPercent.setScale(2);
    }

    public BigDecimal getGainPercent() {
        return gainPercent;
    }

    public void setGain(BigDecimal gain) {
        this.gain = gain;
    }

    public BigDecimal getGain() {
        return gain;
    }

    public String getGainPercentHTML() {
        return FinancialUtils.printGainPercentHTML(gainPercent);
    }

    public String getGainHTML() {
        return FinancialUtils.printGainHTML(gain);
    }

    public String getChangeHTML() {
        String htmlString, arrow;
        if (change < 0.0) {
            htmlString = "<FONT color=\"#cc0000\">";
            arrow = "arrowdown.gif";
        } else {
            htmlString = "<FONT color=\"#009900\">";
            arrow = "arrowup.gif";
        }
        DecimalFormat df = new DecimalFormat("####0.00");

        htmlString += df.format(change) + "</FONT><IMG src=\"images/" + arrow + "\" width=\"10\" height=\"10\" border=\"0\"></IMG>";
        return htmlString;
    }
}
