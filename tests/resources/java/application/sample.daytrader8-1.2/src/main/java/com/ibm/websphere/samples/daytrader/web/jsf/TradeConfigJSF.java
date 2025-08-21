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

import javax.enterprise.context.RequestScoped;
import javax.faces.context.ExternalContext;
import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.daytrader.beans.RunStatsDataBean;
import com.ibm.websphere.samples.daytrader.impl.direct.TradeDirectDBUtils;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

@Named("tradeconfig")
@RequestScoped
public class TradeConfigJSF {

  @Inject
  private ExternalContext context;

  @Inject 
  TradeDirectDBUtils dbUtils;

  private String runtimeMode = TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()];
  private String orderProcessingMode = TradeConfig.getOrderProcessingModeNames()[TradeConfig.getOrderProcessingMode()];
  private int maxUsers = TradeConfig.getMAX_USERS();
  private int maxQuotes = TradeConfig.getMAX_QUOTES();
  private int marketSummaryInterval = TradeConfig.getMarketSummaryInterval();
  private String webInterface = TradeConfig.getWebInterfaceNames()[TradeConfig.getWebInterface()];
  private int primIterations = TradeConfig.getPrimIterations();
  private int listQuotePriceChangeFrequency = TradeConfig.getListQuotePriceChangeFrequency();
  private boolean publishQuotePriceChange = TradeConfig.getPublishQuotePriceChange();
  private boolean longRun = TradeConfig.getLongRun();
  private boolean displayOrderAlerts = TradeConfig.getDisplayOrderAlerts();
  private String[] runtimeModeList = TradeConfig.getRunTimeModeNames();
  private String[] orderProcessingModeList = TradeConfig.getOrderProcessingModeNames();

  private String[] webInterfaceList = TradeConfig.getWebInterfaceNames();
  private String result = "";

  public void updateConfig() {
    String currentConfigStr = "\n\n########## Trade configuration update. Current config:\n\n";     

    currentConfigStr += "\t\tRunTimeMode:\t\t\t" + TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()] + "\n";

    String orderProcessingModeStr = this.orderProcessingMode;
    if (orderProcessingModeStr != null) {
      try {
        for (int i = 0; i < orderProcessingModeList.length; i++) {
          if (orderProcessingModeStr.equals(orderProcessingModeList[i])) {
            TradeConfig.setOrderProcessingMode(i);
          }
        }
      } catch (Exception e) {
        Log.error(e, "TradeConfigJSF.updateConfig(..): minor exception caught", "trying to set orderProcessing to " + orderProcessingModeStr,
            "reverting to current value");

      } // If the value is bad, simply revert to current
    }
    currentConfigStr += "\t\tOrderProcessingMode:\t\t" + TradeConfig.getOrderProcessingModeNames()[TradeConfig.getOrderProcessingMode()] + "\n";

    String webInterfaceStr = webInterface;
    if (webInterfaceStr != null) {
      try {
        for (int i = 0; i < webInterfaceList.length; i++) {
          if (webInterfaceStr.equals(webInterfaceList[i])) {
            TradeConfig.setWebInterface(i);
          }
        }
      } catch (Exception e) {
        Log.error(e, "TradeConfigJSF.updateConfig(..): minor exception caught", "trying to set WebInterface to " + webInterfaceStr,
            "reverting to current value");

      } // If the value is bad, simply revert to current
    }
    currentConfigStr += "\t\tWeb Interface:\t\t\t" + TradeConfig.getWebInterfaceNames()[TradeConfig.getWebInterface()] + "\n";

    TradeConfig.setMAX_USERS(maxUsers);
    TradeConfig.setMAX_QUOTES(maxQuotes);

    currentConfigStr += "\t\tTrade  Users:\t\t\t" + TradeConfig.getMAX_USERS() + "\n";
    currentConfigStr += "\t\tTrade Quotes:\t\t\t" + TradeConfig.getMAX_QUOTES() + "\n";

    TradeConfig.setMarketSummaryInterval(marketSummaryInterval);

    currentConfigStr += "\t\tMarket Summary Interval:\t" + TradeConfig.getMarketSummaryInterval() + "\n";

    TradeConfig.setPrimIterations(primIterations);

    currentConfigStr += "\t\tPrimitive Iterations:\t\t" + TradeConfig.getPrimIterations() + "\n";

    TradeConfig.setPublishQuotePriceChange(publishQuotePriceChange);
    currentConfigStr += "\t\tTradeStreamer MDB Enabled:\t" + TradeConfig.getPublishQuotePriceChange() + "\n";

    TradeConfig.setListQuotePriceChangeFrequency(listQuotePriceChangeFrequency);
    currentConfigStr += "\t\t% of trades on Websocket:\t" + TradeConfig.getListQuotePriceChangeFrequency() + "\n";

    TradeConfig.setLongRun(longRun);
    currentConfigStr += "\t\tLong Run Enabled:\t\t" + TradeConfig.getLongRun() + "\n";

    TradeConfig.setDisplayOrderAlerts(displayOrderAlerts);
    currentConfigStr += "\t\tDisplay Order Alerts:\t\t" + TradeConfig.getDisplayOrderAlerts() + "\n";

    System.out.println(currentConfigStr);
    setResult("DayTrader Configuration Updated");
  }

  public String resetTrade() {
    RunStatsDataBean runStatsData = new RunStatsDataBean();
    TradeConfig currentConfig = new TradeConfig();
    HttpSession session = (HttpSession) context.getSession(true);   


    try {     
      runStatsData = dbUtils.resetTrade(false);
      session.setAttribute("runStatsData", runStatsData);
      session.setAttribute("tradeConfig", currentConfig);
      result += "Trade Reset completed successfully";

    } catch (Exception e) {
      result += "Trade Reset Error  - see log for details";
      session.setAttribute("result", result);
      Log.error(e, result);
    }

    return "stats";
  }

  public String populateDatabase() {

    try {
      dbUtils.buildDB(new java.io.PrintWriter(System.out), null);
    } catch (Exception e) {
      e.printStackTrace();
    }       

    result = "TradeBuildDB: **** DayTrader Database Built - " + TradeConfig.getMAX_USERS() + " users created, " + TradeConfig.getMAX_QUOTES()
    + " quotes created. ****<br/>";
    result += "TradeBuildDB: **** Check System.Out for any errors. ****<br/>";

    return "database";
  }

  public String buildDatabaseTables() {
    try {
      String dbProductName = null;
      try {
        dbProductName = dbUtils.checkDBProductName();
      } catch (Exception e) {
        Log.error(e, "TradeBuildDB: Unable to check DB Product name");
      }
      if (dbProductName == null) {
        result += "TradeBuildDB: **** Unable to check DB Product name, please check Database/AppServer configuration and retry ****<br/>";
        return "database";
      }

      String ddlFile = null;
      //Locate DDL file for the specified database
      try {
        result = result + "TradeBuildDB: **** Database Product detected: " + dbProductName + " ****<br/>";
        if (dbProductName.startsWith("DB2/")) { // if db is DB2
          ddlFile = "/dbscripts/db2/Table.ddl";
        } else if (dbProductName.startsWith("Apache Derby")) { //if db is Derby
          ddlFile = "/dbscripts/derby/Table.ddl";
        } else if (dbProductName.startsWith("Oracle")) { // if the Db is Oracle
          ddlFile = "/dbscripts/oracle/Table.ddl";
        } else { // Unsupported "Other" Database
          ddlFile = "/dbscripts/other/Table.ddl";
          result = result + "TradeBuildDB: **** This Database is unsupported/untested use at your own risk ****<br/>";
        }

        result = result + "TradeBuildDB: **** The DDL file at path" + ddlFile + " will be used ****<br/>";
      } catch (Exception e) {
        Log.error(e, "TradeBuildDB: Unable to locate DDL file for the specified database");
        result = result + "TradeBuildDB: **** Unable to locate DDL file for the specified database ****<br/>";
        return "database";
      }

      dbUtils.buildDB(new java.io.PrintWriter(System.out), context.getResourceAsStream(ddlFile));

      result = result + "TradeBuildDB: **** DayTrader Database Created, Check System.Out for any errors. ****<br/>";

    } catch (Exception e) {
      e.printStackTrace();
    }

    // Go to configure.xhtml
    return "database";
  }




  public String getRuntimeMode() {
    return runtimeMode;
  }

  public void setRuntimeMode(String runtimeMode) {
    this.runtimeMode = runtimeMode;
  }

  public void setOrderProcessingMode(String orderProcessingMode) {
    this.orderProcessingMode = orderProcessingMode;
  }

  public String getOrderProcessingMode() {
    return orderProcessingMode;
  }


  public void setMaxUsers(int maxUsers) {
    this.maxUsers = maxUsers;
  }

  public int getMaxUsers() {
    return maxUsers;
  }

  public void setmaxQuotes(int maxQuotes) {
    this.maxQuotes = maxQuotes;
  }

  public int getMaxQuotes() {
    return maxQuotes;
  }

  public void setMarketSummaryInterval(int marketSummaryInterval) {
    this.marketSummaryInterval = marketSummaryInterval;
  }

  public int getMarketSummaryInterval() {
    return marketSummaryInterval;
  }

  public void setPrimIterations(int primIterations) {
    this.primIterations = primIterations;
  }

  public int getPrimIterations() {
    return primIterations;
  }

  public void setPublishQuotePriceChange(boolean publishQuotePriceChange) {
    this.publishQuotePriceChange = publishQuotePriceChange;
  }

  public boolean isPublishQuotePriceChange() {
    return publishQuotePriceChange;
  }

  public void setListQuotePriceChangeFrequency(int listQuotePriceChangeFrequency) {
    this.listQuotePriceChangeFrequency =  listQuotePriceChangeFrequency;
  }

  public int getListQuotePriceChangeFrequency() {
    return  listQuotePriceChangeFrequency;
  }

  public void setDisplayOrderAlerts(boolean displayOrderAlerts) {
    this.displayOrderAlerts = displayOrderAlerts;
  }

  public boolean isDisplayOrderAlerts() {
    return displayOrderAlerts;
  }


  public void setLongRun(boolean longRun) {
    this.longRun = longRun;
  }

  public boolean isLongRun() {
    return longRun;
  }

  public String[] getRuntimeModeList() {
    return runtimeModeList;
  }

  public void setRuntimeModeList(String[] runtimeModeList) {
    this.runtimeModeList = runtimeModeList;
  }

  public void setOrderProcessingModeList(String[] orderProcessingModeList) {
    this.orderProcessingModeList = orderProcessingModeList;
  }

  public String[] getOrderProcessingModeList() {
    return orderProcessingModeList;
  }

  public void setWebInterface(String webInterface) {
    this.webInterface = webInterface;
  }

  public String getWebInterface() {
    return webInterface;
  }

  public void setWebInterfaceList(String[] webInterfaceList) {
    this.webInterfaceList = webInterfaceList;
  }

  public String[] getWebInterfaceList() {
    return webInterfaceList;
  }

  public void setResult(String result) {
    this.result = result;
  }

  public String getResult() {
    return result;
  }

}
