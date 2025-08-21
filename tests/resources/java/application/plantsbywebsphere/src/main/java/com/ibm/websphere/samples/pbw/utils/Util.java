//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.utils;

import java.io.FileNotFoundException;
import java.text.NumberFormat;
import java.util.StringTokenizer;

import javax.faces.application.Application;
import javax.faces.application.ProjectStage;
import javax.faces.context.FacesContext;
import javax.naming.InitialContext;
import javax.naming.NamingException;

/**
 *  Utility class.
 */
public class Util {
    /** Datasource name. */
    public static final String DS_NAME = "java:comp/env/jdbc/PlantsByWebSphereDataSource";
    // Constants for JSPs and HTMLs.
    public static final String PAGE_ACCOUNT = "account.jsp";
    public static final String PAGE_CART = "cart.jsp";
    public static final String PAGE_CHECKOUTFINAL = "checkout_final.jsp";
    public static final String PAGE_HELP = "help.jsp";
    public static final String PAGE_LOGIN = "login.jsp";
    public static final String PAGE_ORDERDONE = "orderdone.jsp";
    public static final String PAGE_ORDERINFO = "orderinfo.jsp";
    public static final String PAGE_PRODUCT = "product.jsp";
    public static final String PAGE_PROMO = "promo.html";
    public static final String PAGE_REGISTER = "register.jsp";
    public static final String PAGE_SHOPPING = "shopping.jsp";
    public static final String PAGE_BACKADMIN = "backorderadmin.jsp";
    public static final String PAGE_SUPPLIERCFG = "supplierconfig.jsp";
    public static final String PAGE_ADMINHOME = "admin.html";
    public static final String PAGE_ADMINACTIONS = "adminactions.html";
    // Request and session attributes.
    public static final String ATTR_ACTION = "action";
    public static final String ATTR_CART = "ShoppingCart";
//    public static final String ATTR_CART_CONTENTS = "CartContents";
    public static final String ATTR_CARTITEMS = "cartitems";
    public static final String ATTR_CATEGORY = "Category";
    public static final String ATTR_CHECKOUT = "CheckingOut";
    public static final String ATTR_CUSTOMER = "CustomerInfo";
    public static final String ATTR_EDITACCOUNTINFO = "EditAccountInfo";
    public static final String ATTR_INVITEM = "invitem";
    public static final String ATTR_INVITEMS = "invitems";
    public static final String ATTR_ORDERID = "OrderID";
    public static final String ATTR_ORDERINFO = "OrderInfo";
    public static final String ATTR_ORDERKEY = "OrderKey";
    public static final String ATTR_RESULTS = "results";
    public static final String ATTR_UPDATING = "updating";
    public static final int    ATTR_SFTIMEOUT = 10;				// if this is changed, updated session timeout
    															// in the PlantsByWebSphere web.xml
    public static final String ATTR_SUPPLIER = "SupplierInfo";
    // Admin type actions
    public static final String ATTR_ADMINTYPE = "admintype";
    public static final String ADMIN_BACKORDER = "backorder";
    public static final String ADMIN_SUPPLIERCFG = "supplierconfig";
    public static final String ADMIN_POPULATE = "populate";
    // Servlet action codes.
    // Supplier Config actions
    public static final String ACTION_GETSUPPLIER = "getsupplier";
    public static final String ACTION_UPDATESUPPLIER = "updatesupplier";
    // Backorder actions
    public static final String ACTION_ORDERSTOCK = "orderstock";
    public static final String ACTION_UPDATESTOCK = "updatestock";
    public static final String ACTION_GETBACKORDERS = "getbackorders";
    public static final String ACTION_UPDATEQUANTITY = "updatequantity";
    public static final String ACTION_ORDERSTATUS = "orderstatus";
    public static final String ACTION_CANCEL = "cancel";
    public static final String STATUS_ORDERSTOCK = "Order Stock";
    public static final String STATUS_ORDEREDSTOCK = "Ordered Stock";
    public static final String STATUS_RECEIVEDSTOCK = "Received Stock";
    public static final String STATUS_ADDEDSTOCK = "Added Stock";
    public static final String DEFAULT_SUPPLIERID = "Supplier";
    private static InitialContext initCtx = null;
    private static final String[] CATEGORY_STRINGS = { "Flowers", "Fruits & Vegetables", "Trees", "Accessories" };
    private static final String[] SHIPPING_METHOD_STRINGS = { "Standard Ground", "Second Day Air", "Next Day Air" };
    private static final String[] SHIPPING_METHOD_TIMES = { "( 3 to 6 business days )", "( 2 to 3 business days )", "( 1 to 2 business days )" };
    private static final float[] SHIPPING_METHOD_PRICES = { 4.99f, 8.99f, 12.99f };
    public static final String ZERO_14 = "00000000000000";
    /**
     * Return the cached Initial Context.
     *
     * @return InitialContext, or null if a naming exception.
     */
    static public InitialContext getInitialContext() {
        try {
            // Get InitialContext if it has not been gotten yet.
            if (initCtx == null) {
                // properties are in the system properties
                initCtx = new InitialContext();
            }
        }
        // Naming Exception will cause a null return.
        catch (NamingException e) {}
        return initCtx;
    }

    /**
     * Get the displayable name of a category.
     * @param index The int representation of a category.
     * @return The category as a String (null, if an invalid index given).
     */
    static public String getCategoryString(int index) {
        if ((index >= 0) && (index < CATEGORY_STRINGS.length))
            return CATEGORY_STRINGS[index];
        else
            return null;
    }
    /**
     * Get the category strings in an array.
     *
     * @return The category strings in an array.
     */
    static public String[] getCategoryStrings() {
        return CATEGORY_STRINGS;
    }
    /**
     * Get the shipping method.
     * @param index The int representation of a shipping method.
     * @return The shipping method (null, if an invalid index given).
     */
    static public String getShippingMethod(int index) {
        if ((index >= 0) && (index < SHIPPING_METHOD_STRINGS.length))
            return SHIPPING_METHOD_STRINGS[index];
        else
            return null;
    }
    /**
     * Get the shipping method price.
     * @param index The int representation of a shipping method.
     * @return The shipping method price (-1, if an invalid index given).
     */
    static public float getShippingMethodPrice(int index) {
        if ((index >= 0) && (index < SHIPPING_METHOD_PRICES.length))
            return SHIPPING_METHOD_PRICES[index];
        else
            return -1;
    }
    /**
     * Get the shipping method price.
     * @param index The int representation of a shipping method.
     * @return The shipping method time (null, if an invalid index given).
     */
    static public String getShippingMethodTime(int index) {
        if ((index >= 0) && (index < SHIPPING_METHOD_TIMES.length))
            return SHIPPING_METHOD_TIMES[index];
        else
            return null;
    }
    /**
     * Get the shipping method strings in an array.
     * @return The shipping method strings in an array.
     */
    static public String[] getShippingMethodStrings() {
        return SHIPPING_METHOD_STRINGS;
    }
    /**
     * Get the shipping method strings, including prices and times, in an array.
     * @return The shipping method strings, including prices and times, in an array.
     */
    static public String[] getFullShippingMethodStrings() {
        String[] shippingMethods = new String[SHIPPING_METHOD_STRINGS.length];
        for (int i = 0; i < shippingMethods.length; i++) {
            shippingMethods[i] = SHIPPING_METHOD_STRINGS[i] + " " + SHIPPING_METHOD_TIMES[i] + " " + NumberFormat.getCurrencyInstance(java.util.Locale.US).format(new Float(SHIPPING_METHOD_PRICES[i]));
        }
        return shippingMethods;
    }
    private static final String PBW_PROPERTIES = "pbw.properties";
    private static ListProperties PBW_Properties = null;
    /**
     * Method readProperties.
     */
    public static void readProperties() throws FileNotFoundException {
        if (PBW_Properties == null) {
            // Try to read the  properties file.
            ListProperties prop = new ListProperties();
            try {
                String PBW_Properties_File = PBW_PROPERTIES;
                debug("Util.readProperties(): Loading PBW Properties from file: " + PBW_Properties_File);
                prop.load(Util.class.getClassLoader().getResourceAsStream(PBW_Properties_File));
            } catch (Exception e) {
                debug("Util.readProperties(): Exception: " + e);
                // Reset properties to retry loading next time.
                PBW_Properties = null;
                e.printStackTrace();
                throw new FileNotFoundException();
            }
            PBW_Properties = prop;
        }
    }
    /**
     * Method getProperty.
     * @param name
     * @return value
     */
    public static String getProperty(String name) {
        String value = "";
        try {
            if (PBW_Properties == null) {
                readProperties();
            }
            value = PBW_Properties.getProperty(name);
        } catch (Exception e) {
            debug("Util.getProperty(): Exception: " + e);
        }
        return (value);
    }
    /**
     * Method readTokens.
     * @param text
     * @param token
     * @return list
     */
    public static String[] readTokens(String text, String token) {
        StringTokenizer parser = new StringTokenizer(text, token);
        int numTokens = parser.countTokens();
        String[] list = new String[numTokens];
        for (int i = 0; i < numTokens; i++) {
            list[i] = parser.nextToken();
        }
        return list;
    }
    /**
     * Method getProperties.
     * @param name
     * @return values
     */
    public static String[] getProperties(String name) {
        String[] values = { "" };
        try {
            if (PBW_Properties == null) {
                readProperties();
            }
            values = PBW_Properties.getProperties(name);
            debug("Util.getProperties: property (" + name + ") -> " + values.toString());
            //for (Enumeration e = PBW_Properties.propertyNames() ; e.hasMoreElements() ;) {
            //    debug((String)e.nextElement());
            //}
        } catch (Exception e) {
            debug("Util.getProperties(): Exception: " + e);
        }
        return (values);
    }
    static private boolean debug = false;
    /** Set debug setting to on or off.
     * @param val True or false.
     */
    static final public void setDebug(boolean val) {
        debug = val;
    }
    /** Is debug turned on? */
    static final public boolean debugOn() {
        return debug;
    }
    /**
     * Output RAS message.
     * @param msg Message to be output.
     */
    static final public void debug(String msg) {
        FacesContext context = FacesContext.getCurrentInstance();
        if (context != null) {
        	Application app = context.getApplication();
        	if (app != null) {
        		ProjectStage stage = app.getProjectStage();
        		if (stage == ProjectStage.Development || stage == ProjectStage.UnitTest) {
        			setDebug(true);
        		}
        	}
        	if (debug) {
        		System.out.println(msg);
        	}
        }
    }

    /**
     * Utilty functions for validating user input.
     * validateString will return false if any of the invalid characters appear in the input string.
     *
     * In general, we do not want to allow special characters in user input,
     * because this can open us to a XSS security vulnerability.
     * For example, a user should not be allowed to enter javascript in an input field.
     */
	static final char[] invalidCharList={'|','&',';','$','%','\'','\"','\\','<','>',','};

	public static boolean validateString(String input){
		if (input==null) return true;
		for (int i=0;i<invalidCharList.length;i++){
			if (input.indexOf(invalidCharList[i])!=-1){
				return false;
			}
		}
		return true;
	}
}
